"""
Pure logic (no network): turns raw Solana data into a simple, explained safety score.
Everything here is easy to unit-test (see tests/test_safety.py).
"""
import time

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# A few well-known program-controlled holders, so the UI can show a friendly name.
KNOWN_HOLDERS = {
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1": "Raydium AMM v4 pool authority",
    "GpMZbSM2GgvTKHJirzeGfMFoaZ8UR2X7F4v8vHTvxFbL": "Raydium CPMM pool authority",
}

# ----------------------------------------------------------------------------------------------
# 1. "Is this address a normal wallet or a program-controlled account (PDA)?"
#    PDAs are, by design, NOT points on the ed25519 curve, so nobody has a private key for them.
#    Pools, bonding curves, lockers and vaults are PDAs. Normal people's wallets are on the curve.
# ----------------------------------------------------------------------------------------------
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + out


def _raw_bytes(account_value: dict):
    """Returns raw account bytes if the RPC answered in base64 (Solami ignores jsonParsed in getMultipleAccounts)."""
    data = account_value.get("data") if account_value else None
    if isinstance(data, list) and len(data) == 2 and data[1] == "base64":
        import base64
        return base64.b64decode(data[0])
    return None


def token_account_owner(account_value: dict):
    """Owner (wallet/PDA) of an SPL token account, from jsonParsed OR raw base64 (bytes 32..64)."""
    if not account_value:
        return None
    raw = _raw_bytes(account_value)
    if raw is not None:
        return b58encode(raw[32:64]) if len(raw) >= 64 else None
    try:
        return account_value["data"]["parsed"]["info"]["owner"]
    except (TypeError, KeyError):
        return None


def parse_mint_raw(raw: bytes) -> dict:
    """SPL mint layout: COption<Pubkey> mint_authority, u64 supply, u8 decimals, bool init, COption<Pubkey> freeze."""
    if len(raw) < 82:
        raise ValueError("This address is not an SPL token mint.")
    mint_auth = b58encode(raw[4:36]) if int.from_bytes(raw[0:4], "little") == 1 else None
    freeze_auth = b58encode(raw[50:82]) if int.from_bytes(raw[46:50], "little") == 1 else None
    return {"decimals": raw[44], "supply": str(int.from_bytes(raw[36:44], "little")),
            "mintAuthority": mint_auth, "freezeAuthority": freeze_auth, "isInitialized": bool(raw[45])}


def is_valid_pubkey(s: str) -> bool:
    try:
        return 32 <= len(s) <= 44 and len(b58decode(s)) == 32
    except ValueError:
        return False


_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def is_on_curve(pubkey: str) -> bool:
    """Same test Solana uses (ed25519 point decompression). True = wallet-like, False = PDA."""
    b = b58decode(pubkey)
    y = (int.from_bytes(b, "little") & ((1 << 255) - 1)) % _P
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    x = (u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    return vx2 == u or vx2 == (-u) % _P


def classify_holder(owner: str) -> str:
    if owner in KNOWN_HOLDERS:
        return "pool"
    return "wallet" if is_on_curve(owner) else "program"


# ----------------------------------------------------------------------------------------------
# 2. Mint account checks
# ----------------------------------------------------------------------------------------------
def analyse_mint(account_value: dict) -> dict:
    """account_value = result['value'] from getAccountInfo(mint, {encoding: jsonParsed})."""
    if not account_value:
        raise ValueError("Account not found on mainnet.")
    program_owner = account_value.get("owner")
    data = account_value.get("data")
    if program_owner not in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        raise ValueError("This address is not an SPL token mint.")
    raw = _raw_bytes(account_value)
    if raw is not None:  # base64 answer: decode the mint layout ourselves
        if len(raw) == 165 or (len(raw) > 165 and raw[165:166] == b"\x02"):
            raise ValueError("This address is a token account, not a mint. Paste the token's mint address.")
        parsed = {"type": "mint", "info": parse_mint_raw(raw)}
    elif isinstance(data, dict):
        parsed = data.get("parsed", {})
    else:
        raise ValueError("This address is not an SPL token mint.")
    if parsed.get("type") != "mint":
        raise ValueError("This address is a token account, not a mint. Paste the token's mint address.")
    info = parsed.get("info", {})
    decimals = int(info.get("decimals", 0))
    supply_raw = int(info.get("supply", "0"))
    extensions = {e.get("extension"): e.get("state", {}) for e in info.get("extensions", []) or []}
    return {
        "program": "Token-2022" if program_owner == TOKEN_2022_PROGRAM else "SPL Token",
        "decimals": decimals,
        "supply_raw": supply_raw,
        "supply": supply_raw / (10 ** decimals) if decimals else supply_raw,
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
        "extensions": extensions,
    }


# ----------------------------------------------------------------------------------------------
# 3. Holder concentration
# ----------------------------------------------------------------------------------------------
def analyse_holders(largest: list, owners: dict, supply_raw: int) -> dict:
    """
    largest = getTokenLargestAccounts(...)['value']  (token accounts, biggest first)
    owners  = {token_account_address: owner_address} from getMultipleAccounts(jsonParsed)
    """
    rows = []
    for acc in largest[:20]:
        amount = int(acc.get("amount", "0"))
        owner = owners.get(acc["address"])
        kind = classify_holder(owner) if owner else "unknown"
        rows.append({
            "token_account": acc["address"],
            "owner": owner,
            "kind": kind,
            "label": KNOWN_HOLDERS.get(owner or "", ""),
            "pct": (amount / supply_raw * 100) if supply_raw else 0.0,
        })
    wallets = [r for r in rows if r["kind"] in ("wallet", "unknown")]
    return {
        "rows": rows,
        "top1_wallet_pct": max((r["pct"] for r in wallets), default=0.0),
        "top10_wallet_pct": sum(r["pct"] for r in wallets[:10]),
        "program_pct": sum(r["pct"] for r in rows if r["kind"] in ("pool", "program")),
    }


# ----------------------------------------------------------------------------------------------
# 4. Live activity from recent signatures
# ----------------------------------------------------------------------------------------------
def analyse_activity(signatures: list, now=None) -> dict:
    now = now or time.time()
    times = [s["blockTime"] for s in signatures if s.get("blockTime")]
    failed = sum(1 for s in signatures if s.get("err"))
    last = max(times) if times else None
    return {
        "sampled": len(signatures),
        "tx_1m": sum(1 for t in times if now - t <= 60),
        "tx_5m": sum(1 for t in times if now - t <= 300),
        "tx_1h": sum(1 for t in times if now - t <= 3600),
        "failed_pct": (failed / len(signatures) * 100) if signatures else 0.0,
        "last_tx_seconds_ago": round(now - last) if last else None,
        "capped": len(signatures) >= 100,  # we only look at the newest 100
    }


# ----------------------------------------------------------------------------------------------
# 5. Score
# ----------------------------------------------------------------------------------------------
def score(mint: dict, holders: dict | None, holders_note: str | None = None) -> dict:
    findings = []  # (points_removed, level, text)

    def add(points, level, text):
        findings.append({"points": points, "level": level, "text": text})

    if mint["mint_authority"]:
        add(30, "high", "Mint authority is ON: the creator can print unlimited new tokens.")
    else:
        add(0, "ok", "Mint authority is revoked: supply is fixed.")
    if mint["freeze_authority"]:
        add(25, "high", "Freeze authority is ON: the creator can freeze your tokens so you cannot sell.")
    else:
        add(0, "ok", "Freeze authority is revoked.")

    ext = mint["extensions"]
    if "permanentDelegate" in ext and (ext["permanentDelegate"] or {}).get("delegate"):
        add(30, "high", "Token-2022 permanent delegate: someone can move or burn tokens from ANY wallet.")
    if "transferHook" in ext and (ext["transferHook"] or {}).get("programId"):
        add(15, "medium", "Token-2022 transfer hook: a custom program runs on every transfer (can block sells).")
    if "transferFeeConfig" in ext:
        st = ext["transferFeeConfig"] or {}
        bps = int(((st.get("newerTransferFee") or {}).get("transferFeeBasisPoints")) or 0)
        if bps > 0:
            add(20 if bps > 500 else 10, "medium", f"Transfer tax of {bps / 100:.2f}% on every transfer.")
    if "defaultAccountState" in ext and (ext["defaultAccountState"] or {}).get("accountState") == "frozen":
        add(20, "high", "New token accounts start FROZEN by default.")
    if "pausable" in ext:
        add(15, "medium", "Token-2022 pausable: transfers can be paused by an authority.")

    if holders:
        t1, t10 = holders["top1_wallet_pct"], holders["top10_wallet_pct"]
        if t1 > 50:
            add(30, "high", f"One wallet holds {t1:.1f}% of supply.")
        elif t1 > 20:
            add(15, "medium", f"One wallet holds {t1:.1f}% of supply.")
        if t10 > 50:
            add(15, "medium", f"Top 10 wallets (pools excluded) hold {t10:.1f}% of supply.")
        if t1 <= 20 and t10 <= 50:
            add(0, "ok", f"Holder spread looks healthy (largest wallet {t1:.1f}%, top 10 wallets {t10:.1f}%).")
    else:
        add(0, "info", holders_note or "Holder data unavailable right now, so concentration was not scored.")

    value = max(0, 100 - sum(f["points"] for f in findings))
    grade = "LOW RISK" if value >= 80 else ("CAUTION" if value >= 50 else "HIGH RISK")
    return {"score": value, "grade": grade, "findings": findings}


# ----------------------------------------------------------------------------------------------
# 6. Helpers for Blur (decoded market data). Field names are read defensively.
# ----------------------------------------------------------------------------------------------
def pick(d, *names, default=None):
    if not isinstance(d, dict):
        return default
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return default


def as_list(payload):
    """Blur endpoints return either a list or {data|items|tokens|trades|results: [...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "items", "tokens", "trades", "results", "launches"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def buy_sell_pressure(trades: list) -> dict:
    buy = sell = 0.0
    traders = set()
    for t in trades:
        try:
            usd = float(pick(t, "volume_usd", "usd", "amount_usd", default=0) or 0)
        except (TypeError, ValueError):
            usd = 0.0
        side = str(pick(t, "side", default="")).lower()
        if side == "buy":
            buy += usd
        elif side == "sell":
            sell += usd
        who = pick(t, "trader", "wallet", "owner", "signer")
        if who:
            traders.add(who)
    total = buy + sell
    return {"count": len(trades), "buy_usd": buy, "sell_usd": sell,
            "buy_share_pct": (buy / total * 100) if total else None,
            "unique_traders": len(traders) or None}
