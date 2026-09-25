"""
MintCheck - live Solana token safety radar, powered by Solami.

Run:  python app.py      then open http://127.0.0.1:8000
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

import safety
from solami_client import SolamiClient, SolamiError

def load_env_file(path=".env"):
    """Tiny .env loader so beginners do not need extra packages."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env_file()

app = Flask(__name__, static_folder="static", static_url_path="/static")
client = SolamiClient()
pool = ThreadPoolExecutor(max_workers=6)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


# ------------------------------------------------------------------------------------------------
# Network + provider health (live on every refresh)
# ------------------------------------------------------------------------------------------------
@app.get("/api/status")
def status():
    out = {"rpc_provider": client.rpc_provider, "has_key": client.has_key, "errors": []}
    try:
        slot, ms = client.timed_rpc("getSlot", [{"commitment": "confirmed"}])
        out.update(slot=slot, rpc_latency_ms=ms)
    except SolamiError as e:
        out["errors"].append(str(e))
    try:
        out.update(network_tps())
    except (SolamiError, KeyError, TypeError) as e:
        out["errors"].append(str(e))
    out["blur"] = blur_available()
    return jsonify(out)


def network_tps():
    """TPS from performance samples; Solami returns [] for those, so fall back to the latest finalized block."""
    samples = client.rpc("getRecentPerformanceSamples", [3], ttl=20) or []
    if samples:
        tx = sum(s["numTransactions"] for s in samples)
        non_vote = sum(s.get("numNonVoteTransactions", 0) for s in samples)
        secs = sum(s["samplePeriodSecs"] for s in samples) or 1
        return {"tps": round(tx / secs), "user_tps": round(non_vote / secs), "tps_source": "performance samples"}
    cached = client.cache.get(("tps_block",))
    if cached:
        return cached
    slot = client.rpc("getSlot", [{"commitment": "finalized"}])
    block = client.rpc("getBlock", [slot, {"transactionDetails": "signatures", "rewards": False,
                                           "maxSupportedTransactionVersion": 1, "commitment": "finalized"}])
    slot_secs = client.cache.get(("slot_secs",))
    if not slot_secs:
        t_now, t_old = client.rpc("getBlockTime", [slot]), client.rpc("getBlockTime", [slot - 300])
        slot_secs = max(0.2, min(1.0, (t_now - t_old) / 300)) if t_now and t_old else 0.4
        client.cache.set(("slot_secs",), slot_secs, 300)
    n = len(block.get("signatures") or [])
    result = {"tps": round(n / slot_secs), "user_tps": None,
              "tps_source": f"latest finalized block: {n} tx, slot time {slot_secs:.2f} s"}
    client.cache.set(("tps_block",), result, 15)
    return result


_blur_probe = {"until": 0, "ok": False, "msg": ""}


def blur_available():
    """Checks (at most once a minute) whether the key can use Blur decoded data."""
    if not client.has_key:
        return {"ok": False, "msg": "No Solami key: Blur features off."}
    if _blur_probe["until"] > time.time():
        return {"ok": _blur_probe["ok"], "msg": _blur_probe["msg"]}
    try:
        client.data("/data/token/trending", {"window": 3600, "limit": 1}, ttl=30)
        _blur_probe.update(ok=True, msg="Blur decoded data connected.")
    except SolamiError as e:
        msg = str(e)
        if "unauthorized" in msg.lower() or "permission" in msg.lower():
            msg = "Blur decoded data needs the DataApi permission (Solami Pro / Pro trial). Free plan: RPC features only."
        _blur_probe.update(ok=False, msg=msg)
    _blur_probe["until"] = time.time() + 60
    return {"ok": _blur_probe["ok"], "msg": _blur_probe["msg"]}


# ------------------------------------------------------------------------------------------------
# Full safety report for one token
# ------------------------------------------------------------------------------------------------
@app.get("/api/check")
def check():
    mint_addr = (request.args.get("mint") or "").strip()
    if not safety.is_valid_pubkey(mint_addr):
        return jsonify({"error": "That does not look like a Solana address."}), 400

    report = {"mint": mint_addr, "errors": [], "checked_at": int(time.time()),
              "data_path": {"rpc": client.rpc_provider, "blur": False}}

    # 1) mint account (1 RPC call)
    try:
        acc = client.rpc("getAccountInfo", [mint_addr, {"encoding": "jsonParsed"}], ttl=15)
        report["token"] = safety.analyse_mint(acc.get("value"))
    except (SolamiError, ValueError) as e:
        return jsonify({"error": str(e)}), 400

    # 2) holders + 3) activity, in parallel (3 RPC calls total)
    def get_holders():
        largest = client.rpc("getTokenLargestAccounts", [mint_addr, {"commitment": "confirmed"}], ttl=30)["value"]
        addrs = [a["address"] for a in largest[:20]]
        infos = client.rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}], ttl=30)["value"]
        owners = {addr: safety.token_account_owner(info) for addr, info in zip(addrs, infos)}
        owners = {k: v for k, v in owners.items() if v}
        return safety.analyse_holders(largest, owners, report["token"]["supply_raw"])

    def get_activity():
        sigs = client.rpc("getSignaturesForAddress", [mint_addr, {"limit": 100, "commitment": "confirmed"}], ttl=5)
        return safety.analyse_activity(sigs)

    f_holders, f_activity = pool.submit(get_holders), pool.submit(get_activity)
    holders_note = None
    for name, fut in (("holders", f_holders), ("activity", f_activity)):
        try:
            report[name] = fut.result()
        except (SolamiError, KeyError, TypeError) as e:
            report[name] = None
            if name == "holders" and "scan budget" in str(e):
                # Solami refuses getTokenLargestAccounts for mints with a huge number of holder accounts.
                holders_note = ("This token has too many holder accounts to scan in real time "
                                "(typical for large, established tokens), so concentration was not scored.")
            else:
                report["errors"].append(f"{name}: {e}")
    report["holders_note"] = holders_note
    report["risk"] = safety.score(report["token"], report.get("holders"), holders_note)

    # 4) Blur decoded market data (optional, needs DataApi permission)
    if blur_available()["ok"]:
        report["data_path"]["blur"] = True
        report["market"] = blur_market(mint_addr, report["errors"])
    return jsonify(report)


def blur_market(mint_addr, errors):
    now = int(time.time())
    jobs = {
        "metadata": ("/data/token/metadata", {"address": mint_addr}),
        "stats": ("/data/token/stats", {"address": mint_addr, "windows": "300,3600,86400"}),
        "trades": ("/data/token/trades", {"chain": "solana", "address": mint_addr, "limit": 100,
                                          "after_time": now - 300, "before_time": now + 1}),
        "security": ("/data/token/security", {"address": mint_addr}),
    }
    futures = {k: pool.submit(client.data, path, params) for k, (path, params) in jobs.items()}
    market = {}
    for k, fut in futures.items():
        try:
            market[k] = fut.result()
        except SolamiError as e:
            market[k] = None
            errors.append(f"blur {k}: {e}")
    trades = safety.as_list(market.get("trades"))
    market["pressure_5m"] = safety.buy_sell_pressure(trades)
    market["recent_trades"] = trades[:15]
    market.pop("trades", None)
    meta = market.get("metadata") or {}
    if isinstance(meta, list) and meta:
        meta = meta[0]
    market["name"] = safety.pick(meta, "name")
    market["symbol"] = safety.pick(meta, "symbol")
    return market


# ------------------------------------------------------------------------------------------------
# Live feeds: new launches / trending, each one auto-checked for mint+freeze authority.
# Cost: 1 Blur call + 1 RPC call (getMultipleAccounts checks all mints at once).
# ------------------------------------------------------------------------------------------------
@app.get("/api/feed")
def feed():
    kind = request.args.get("kind", "launches")
    if not blur_available()["ok"]:
        return jsonify({"items": [], "error": blur_available()["msg"]})
    try:
        if kind == "trending":
            raw = client.data("/data/token/trending", {"window": 3600, "sort": "volume", "limit": 15}, ttl=15)
        else:
            raw = client.data("/data/token/launches", {"limit": 15}, ttl=8)
    except SolamiError as e:
        return jsonify({"items": [], "error": str(e)})

    items = []
    for row in safety.as_list(raw):
        mint_addr = safety.pick(row, "address", "mint", "token", "token_address", "base_mint")
        if isinstance(mint_addr, dict):
            mint_addr = safety.pick(mint_addr, "address", "mint")
        if not mint_addr or not safety.is_valid_pubkey(str(mint_addr)):
            continue
        items.append({"mint": mint_addr,
                      "name": safety.pick(row, "name"), "symbol": safety.pick(row, "symbol"),
                      "time": safety.pick(row, "block_time", "created_at", "timestamp", "time"),
                      "volume_usd": safety.pick(row, "volume_usd", "volume", "volume_24h_usd"),
                      "launchpad": safety.pick(row, "launchpad", "dex", "platform")})
    if items:
        try:
            infos = client.rpc("getMultipleAccounts", [[i["mint"] for i in items], {"encoding": "jsonParsed"}])["value"]
            for item, info in zip(items, infos):
                try:
                    m = safety.analyse_mint(info)
                    item["mint_authority_on"] = bool(m["mint_authority"])
                    item["freeze_authority_on"] = bool(m["freeze_authority"])
                    item["quick_risk"] = safety.score(m, None)["score"]
                except ValueError:
                    pass
        except SolamiError as e:
            return jsonify({"items": items, "error": f"RPC quick-check failed: {e}"})
    return jsonify({"items": items, "raw_sample": safety.as_list(raw)[:1]})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    print(f"MintCheck on http://127.0.0.1:{port}  (RPC: {client.rpc_provider})")
    app.run(host="0.0.0.0", port=port, debug=False)
