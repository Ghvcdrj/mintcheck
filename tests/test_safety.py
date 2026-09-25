import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import safety  # noqa: E402

BONK_MINT_ACCOUNT = {  # real getAccountInfo(jsonParsed) shape, trimmed
    "owner": safety.TOKEN_PROGRAM,
    "data": {"program": "spl-token", "parsed": {"type": "mint", "info": {
        "decimals": 5, "freezeAuthority": None, "mintAuthority": None,
        "isInitialized": True, "supply": "8799438384913327785"}}},
}


class TestCurve(unittest.TestCase):
    def test_pda_is_off_curve(self):
        # Raydium AMM v4 authority is a PDA
        self.assertFalse(safety.is_on_curve("5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"))

    def test_wallet_is_on_curve(self):
        # Solana Foundation-published example wallet addresses are normal keypairs
        self.assertTrue(safety.is_on_curve("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"))

    def test_valid_pubkey(self):
        self.assertTrue(safety.is_valid_pubkey("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"))
        self.assertFalse(safety.is_valid_pubkey("hello"))
        self.assertFalse(safety.is_valid_pubkey("0OIl" * 10))


class TestScore(unittest.TestCase):
    def test_clean_token(self):
        m = safety.analyse_mint(BONK_MINT_ACCOUNT)
        self.assertEqual(m["decimals"], 5)
        r = safety.score(m, {"top1_wallet_pct": 3.0, "top10_wallet_pct": 20.0})
        self.assertEqual(r["score"], 100)
        self.assertEqual(r["grade"], "LOW RISK")

    def test_rug_token(self):
        acc = {"owner": safety.TOKEN_2022_PROGRAM, "data": {"parsed": {"type": "mint", "info": {
            "decimals": 6, "supply": "1000", "mintAuthority": "X", "freezeAuthority": "Y",
            "extensions": [{"extension": "permanentDelegate", "state": {"delegate": "Z"}}]}}}}
        m = safety.analyse_mint(acc)
        r = safety.score(m, {"top1_wallet_pct": 80.0, "top10_wallet_pct": 95.0})
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["grade"], "HIGH RISK")

    def test_not_a_mint(self):
        with self.assertRaises(ValueError):
            safety.analyse_mint({"owner": "11111111111111111111111111111111", "data": ["", "base64"]})


class TestHoldersAndActivity(unittest.TestCase):
    def test_pools_are_excluded(self):
        largest = [{"address": "A", "amount": "600"}, {"address": "B", "amount": "100"}]
        owners = {"A": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # pool
                  "B": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"}  # wallet
        h = safety.analyse_holders(largest, owners, 1000)
        self.assertAlmostEqual(h["top1_wallet_pct"], 10.0)
        self.assertAlmostEqual(h["program_pct"], 60.0)

    def test_activity(self):
        now = 1_000_000
        sigs = [{"blockTime": now - 10}, {"blockTime": now - 200, "err": {"x": 1}}, {"blockTime": now - 4000}]
        a = safety.analyse_activity(sigs, now=now)
        self.assertEqual((a["tx_1m"], a["tx_5m"], a["tx_1h"]), (1, 2, 2))
        self.assertAlmostEqual(a["failed_pct"], 100 / 3)

    def test_pressure(self):
        p = safety.buy_sell_pressure([{"side": "buy", "volume_usd": "30"}, {"side": "sell", "volume_usd": "10"}])
        self.assertEqual(p["buy_share_pct"], 75.0)


class TestBase64Accounts(unittest.TestCase):
    """Solami's getMultipleAccounts answers in base64 even when jsonParsed is requested."""

    def test_token_account_owner(self):
        import base64
        owner = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
        raw = b"\x01" * 32 + safety.b58decode(owner) + (5).to_bytes(8, "little") + b"\x00" * 93
        acc = {"owner": safety.TOKEN_PROGRAM, "data": [base64.b64encode(raw).decode(), "base64"]}
        self.assertEqual(safety.token_account_owner(acc), owner)

    def test_raw_mint(self):
        import base64
        auth = safety.b58decode("9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")
        raw = ((1).to_bytes(4, "little") + auth + (1000).to_bytes(8, "little") + bytes([6, 1])
               + (0).to_bytes(4, "little") + b"\x00" * 32)
        acc = {"owner": safety.TOKEN_PROGRAM, "data": [base64.b64encode(raw).decode(), "base64"]}
        m = safety.analyse_mint(acc)
        self.assertEqual((m["decimals"], m["supply_raw"], m["freeze_authority"]), (6, 1000, None))
        self.assertEqual(m["mint_authority"], "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM")

    def test_b58_roundtrip(self):
        a = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        self.assertEqual(safety.b58encode(safety.b58decode(a)), a)


if __name__ == "__main__":
    unittest.main()
