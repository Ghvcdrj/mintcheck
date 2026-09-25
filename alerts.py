"""
Optional: Telegram alerts for NEW token launches, each one safety-checked through Solami.

Every 15 s it reads new launches from Solami Blur (/data/token/launches), checks all of them in ONE
Solami RPC call (getMultipleAccounts) and posts to your Telegram chat:
  🟢 clean launch  (mint + freeze authority revoked)   or   🔴 risky launch (authority still ON)

Setup: create a bot with @BotFather, put TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env, then
    python alerts.py            (add --only-clean to skip risky ones)
"""
import os
import sys
import time

import requests

import safety
from app import client  # reuses the same Solami client and .env loading
from solami_client import SolamiError

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
SITE = os.getenv("PUBLIC_URL", "http://127.0.0.1:8000")
ONLY_CLEAN = "--only-clean" in sys.argv


def send(text):
    if not (TOKEN and CHAT):
        print(text, "\n")
        return
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True}, timeout=10)


def main():
    seen = set()
    first_round = True
    while True:
        try:
            rows = safety.as_list(client.data("/data/token/launches", {"limit": 20}, ttl=5))
            mints = []
            for row in rows:
                m = safety.pick(row, "address", "mint", "token", "token_address", "base_mint")
                if m and safety.is_valid_pubkey(str(m)) and m not in seen:
                    mints.append((m, row))
            if mints:
                infos = client.rpc("getMultipleAccounts", [[m for m, _ in mints], {"encoding": "jsonParsed"}])["value"]
                for (m, row), info in zip(mints, infos):
                    seen.add(m)
                    if first_round:
                        continue  # do not spam old launches on start
                    try:
                        mint = safety.analyse_mint(info)
                    except ValueError:
                        continue
                    clean = not mint["mint_authority"] and not mint["freeze_authority"]
                    if ONLY_CLEAN and not clean:
                        continue
                    name = safety.pick(row, "symbol", "name", default="new token")
                    send(f"{'🟢 clean' if clean else '🔴 risky'} launch: {name}\n"
                         f"mint authority: {'ON ⛔' if mint['mint_authority'] else 'revoked ✅'}\n"
                         f"freeze authority: {'ON ⛔' if mint['freeze_authority'] else 'revoked ✅'}\n"
                         f"{SITE}/?mint={m}\nhttps://solscan.io/token/{m}")
            first_round = False
        except SolamiError as e:
            print("Solami error:", e)
        time.sleep(15)


if __name__ == "__main__":
    main()
