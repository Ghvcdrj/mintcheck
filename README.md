# 🛡️ MintCheck: live Solana token safety radar (powered by Solami)

Paste any Solana token mint and in about 2 seconds MintCheck reads mainnet **live through Solami** and answers the
questions every buyer should ask before aping in:

| Question | How MintCheck answers it | Solami product |
|---|---|---|
| Can the creator print more tokens? | `getAccountInfo` → mint authority | RPC |
| Can the creator freeze my tokens? | `getAccountInfo` → freeze authority | RPC |
| Hidden Token-2022 traps? | permanent delegate, transfer hook, transfer tax, default-frozen, pausable | RPC |
| Is supply concentrated in a few wallets? | `getTokenLargestAccounts` + `getMultipleAccounts`, then an **ed25519 on-curve test** separates real wallets from program-owned PDAs (pools, bonding curves, lockers) so pools don't cause false alarms | RPC |
| Is anyone actually trading it right now? | `getSignaturesForAddress` → tx in last 1 min / 5 min / 1 h, failed-tx % | RPC |
| Buy or sell pressure? | last-5-min decoded swaps → buy vs sell USD, unique traders, trade list | Blur |
| What just launched, and is it safe? | live **new-launches** and **trending** feeds; every token is auto-checked for mint/freeze authority with **one batched RPC call** | Blur + RPC |
| Network health | slot, RPC latency, network TPS (refreshed every 5 s) | RPC |

The result is one **0–100 safety score** with a plain-English reason for every point removed.

Optional: `alerts.py` pushes 🟢 clean / 🔴 risky **new-launch alerts to Telegram**.

> Not financial advice. A high score only means the obvious traps were not found.

## Screenshot
`screenshot_live.png` shows real mainnet data through **Solami RPC on the free plan** (STREAM token). With a key that has
the `DataApi` permission, the Blur sections (buy/sell pressure, launches and trending feeds) appear automatically;
on the free plan they are hidden, with no errors.

## Free plan vs Pro (tested live, 25 Sep 2026)
| Feature | Free plan (RPC, 5 req/s) | Needs `DataApi` (Pro / Pro trial) |
|---|---|---|
| Mint / freeze authority, Token-2022 checks, score | ✅ | |
| Top holders + wallet-vs-PDA split | ✅ (see note) | |
| Live activity (tx per 1 min / 5 min / 1 h) | ✅ | |
| Slot, latency, network tx/s | ✅ | |
| Buy/sell pressure, trade list, metadata | | ✅ |
| New-launch + trending feeds, Telegram alerts | | ✅ |

Solami quirks the code handles:
- `getTokenLargestAccounts` is refused for mints with a huge number of holder accounts (e.g. BONK, JUP: "exceeds the
  scan budget"). MintCheck shows a clear note and does not score concentration for those tokens. New and mid-size tokens work.
- `getMultipleAccounts` answers in base64 even when `jsonParsed` is requested, so token accounts and mints are decoded
  from raw bytes (`safety.token_account_owner`, `safety.parse_mint_raw`).
- `getRecentPerformanceSamples` returns `[]`, so network tx/s is estimated from the latest finalized block
  (`getBlock` signatures ÷ measured slot time). It is a rough, per-block estimate.
- Blur without permission returns `403 {"message":"missing required permission: DataApi"}`.

## Run it (5 minutes)

Requirements: Python 3.10+.

```bash
git clone <this repo> && cd mintcheck
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then put your key in .env
python app.py                 # open http://127.0.0.1:8000
```

### Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `SOLAMI_API_KEY` | yes | Your key from https://solami.dev (Dashboard → API keys). RPC works on the free plan. Blur features need the `DataApi` permission (Pro / 7-day Pro trial). |
| `RPC_RPS` | no (default 4) | Client-side RPC rate limit. The free plan allows 5 req/s. |
| `RPC_URL` | no | **Local testing only**: any Solana RPC URL, used instead of Solami when you don't have a key yet. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PUBLIC_URL` | no | For `alerts.py`. |

### Point it at your own key
Put your key in `.env` (`SOLAMI_API_KEY=...`) or export it in the shell. The key lives **only on the server**. It is
sent to Solami as `?api_key=` (RPC) or the `x-api-key` header (Blur), is never returned to the browser, and is scrubbed from
error messages.

Endpoints used:
- RPC: `https://rpc.solami.dev/sol?api_key=KEY`
- Blur: `https://api.solami.dev/data/token/{launches,trending,trades,stats,metadata,security}`

## API
| Route | What |
|---|---|
| `GET /api/status` | slot, RPC latency, TPS, whether Blur is reachable with this key |
| `GET /api/check?mint=<address>` | full report: token, risk score + findings, holders, live activity, Blur market data |
| `GET /api/feed?kind=launches\|trending` | live feed, each item quick-checked (mint/freeze authority) |

## Tests
```bash
python -m unittest discover -s tests -v                          # offline unit tests (scoring, PDA detection)
RPC_URL=https://api.mainnet-beta.solana.com python tests/live_smoke.py   # live mainnet smoke test, no key
SOLAMI_API_KEY=... python tests/live_smoke.py                     # full live test through Solami
```

## Deploy for free (Render)
1. Push this repo to GitHub.
2. On https://render.com → New → Blueprint → choose the repo (it reads `render.yaml`).
3. Set `SOLAMI_API_KEY` in the service's Environment tab.
4. Free instances sleep after ~15 min idle; the first request then takes ~30–60 s.

## How the score works
Start at 100 and subtract: mint authority on −30, freeze authority on −25, permanent delegate −30, transfer hook −15,
transfer tax −10/−20, default-frozen accounts −20, pausable −15, one wallet >20% −15 (>50% −30), top-10 wallets >50% −15.
≥80 = LOW RISK, 50–79 = CAUTION, <50 = HIGH RISK. Addresses that are off the ed25519 curve are PDAs controlled by a program
(AMM pools, pump.fun bonding curves, lockers), so they are excluded from wallet concentration.

## Project structure
```
app.py            Flask server + API routes
solami_client.py  Solami RPC + Blur client (rate limit, cache, retries, key scrubbing)
safety.py         pure scoring logic (unit-tested)
alerts.py         optional Telegram launch alerts
static/           index.html, app.js, style.css (no build step)
tests/            unit tests + live smoke test
```

MIT licensed.
