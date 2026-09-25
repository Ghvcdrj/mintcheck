"""
Live smoke test of the whole /api/check flow against mainnet.
Uses the real RPC for every call EXCEPT getTokenLargestAccounts, which public RPCs block;
for that one call we build the list from token accounts seen in a real recent transaction.
With a real SOLAMI_API_KEY nothing is faked:  SOLAMI_API_KEY=... python tests/live_smoke.py
Usage (test mode): RPC_URL=https://api.mainnet-beta.solana.com python tests/live_smoke.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as mintcheck  # noqa: E402

MINT = sys.argv[1] if len(sys.argv) > 1 else "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"
c = mintcheck.client

if not c.has_key:
    real_rpc = c.rpc
    sig = real_rpc("getSignaturesForAddress", [MINT, {"limit": 5}])
    fake_largest = []
    for s in sig:
        tx = real_rpc("getTransaction", [s["signature"], {"maxSupportedTransactionVersion": 0, "encoding": "json"}])
        if not tx:
            continue
        keys = tx["transaction"]["message"]["accountKeys"] + \
            (tx["meta"].get("loadedAddresses", {}).get("writable", []) + tx["meta"].get("loadedAddresses", {}).get("readonly", []))
        for b in tx["meta"].get("postTokenBalances", []):
            if b["mint"] == MINT:
                fake_largest.append({"address": keys[b["accountIndex"]], "amount": b["uiTokenAmount"]["amount"]})
        if len(fake_largest) >= 4:
            break
        time.sleep(0.5)
    fake_largest.sort(key=lambda x: -int(x["amount"]))
    print(f"(test mode) using {len(fake_largest)} real token accounts in place of getTokenLargestAccounts")

    def patched(method, params=None, ttl=0):
        if method == "getTokenLargestAccounts":
            return {"value": fake_largest}
        return real_rpc(method, params, ttl)
    c.rpc = patched

with mintcheck.app.test_client() as t:
    r = t.get(f"/api/check?mint={MINT}").get_json()
    print("errors:", r.get("errors"))
    print("risk:", r["risk"]["score"], r["risk"]["grade"])
    print("activity:", r["activity"])
    for row in (r.get("holders") or {}).get("rows", []):
        print("holder:", row["owner"], row["kind"], f'{row["pct"]:.6f}%')
    s = t.get("/api/status").get_json()
    print("status:", {k: s.get(k) for k in ("rpc_provider", "slot", "rpc_latency_ms", "tps")})
