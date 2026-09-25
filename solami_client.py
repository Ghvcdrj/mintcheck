"""
Small client for Solami (https://solami.dev).

Two data paths are used:
  1. Solami RPC        -> https://rpc.solami.dev/sol?api_key=KEY   (works on the FREE plan, 5 req/s)
  2. Solami Blur data  -> https://api.solami.dev/data/...          (decoded market data, needs the
                                                                      DataApi permission, e.g. Pro trial)

The API key stays on the server. It is never sent to the browser and never written to logs.
"""
import os
import threading
import time

import requests

RPC_BASE = "https://rpc.solami.dev/sol"
DATA_BASE = "https://api.solami.dev"


class RateLimiter:
    """Very small 'max N calls per second' limiter (free plan = 5 RPC calls per second)."""

    def __init__(self, per_second: float):
        self.min_gap = 1.0 / per_second
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            sleep_for = self.last + self.min_gap - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self.last = time.monotonic()


class TTLCache:
    """Remembers answers for a few seconds so many browser tabs do not burn the rate limit."""

    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if item and item[0] > time.time():
                return item[1]
            return None

    def set(self, key, value, ttl):
        with self.lock:
            if len(self.data) > 500:  # keep memory small
                self.data.clear()
            self.data[key] = (time.time() + ttl, value)


class SolamiError(Exception):
    pass


class SolamiClient:
    def __init__(self, api_key=None, rpc_url_override=None, rps=None):
        self.api_key = (api_key if api_key is not None else os.getenv("SOLAMI_API_KEY", "")).strip()
        # RPC_URL is ONLY for local testing without a Solami key (e.g. public mainnet RPC).
        self.rpc_url_override = (rpc_url_override if rpc_url_override is not None
                                 else os.getenv("RPC_URL", "")).strip()
        self.limiter = RateLimiter(float(rps or os.getenv("RPC_RPS", "4")))
        self.cache = TTLCache()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "mintcheck/0.1"

    # ------------------------------------------------------------------ info
    @property
    def rpc_provider(self):
        if self.rpc_url_override:
            return "custom RPC (test mode, not Solami)"
        if self.api_key:
            return "Solami RPC"
        return "not configured"

    @property
    def has_key(self):
        return bool(self.api_key)

    def _scrub(self, text):
        text = str(text)
        if self.api_key:
            text = text.replace(self.api_key, "***")
        return text[:300]

    # ------------------------------------------------------------------ RPC
    def rpc(self, method, params=None, ttl=0):
        cache_key = ("rpc", method, repr(params))
        if ttl:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        if self.rpc_url_override:
            url, query = self.rpc_url_override, {}
        elif self.api_key:
            url, query = RPC_BASE, {"api_key": self.api_key}
        else:
            raise SolamiError("No SOLAMI_API_KEY set (and no RPC_URL for test mode).")

        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        for attempt in range(3):  # retry politely if we hit the rate limit
            self.limiter.wait()
            try:
                r = self.session.post(url, params=query, json=body, timeout=15)
            except requests.RequestException as e:
                raise SolamiError(f"RPC network error: {self._scrub(e)}")
            if r.status_code != 429:
                break
            time.sleep(1.5 * (attempt + 1))
        if r.status_code == 429:
            raise SolamiError("RPC rate limit hit (429). Try again in a few seconds.")
        if r.status_code != 200:
            raise SolamiError(f"RPC HTTP {r.status_code}: {self._scrub(r.text)}")
        payload = r.json()
        if "error" in payload:
            raise SolamiError(f"RPC error in {method}: {self._scrub(payload['error'])}")
        result = payload.get("result")
        if ttl:
            self.cache.set(cache_key, result, ttl)
        return result

    def timed_rpc(self, method, params=None):
        """Same as rpc() but also returns latency in milliseconds (used for the health panel)."""
        start = time.perf_counter()
        result = self.rpc(method, params)
        return result, round((time.perf_counter() - start) * 1000)

    # ------------------------------------------------------------------ Blur / Data API
    def data(self, path, params=None, ttl=10):
        """GET https://api.solami.dev/data/... with the key in the x-api-key header."""
        if not self.api_key:
            raise SolamiError("Blur data needs SOLAMI_API_KEY.")
        cache_key = ("data", path, repr(sorted((params or {}).items())))
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            r = self.session.get(DATA_BASE + path, params=params or {},
                                 headers={"x-api-key": self.api_key}, timeout=15)
        except requests.RequestException as e:
            raise SolamiError(f"Blur network error: {self._scrub(e)}")
        if r.status_code in (401, 403):
            raise SolamiError("Blur says unauthorized: the key needs the DataApi permission "
                              "(included in Pro / the 7-day Pro trial).")
        if r.status_code == 429:
            raise SolamiError("Blur rate limit hit (429).")
        if r.status_code != 200:
            raise SolamiError(f"Blur HTTP {r.status_code}: {self._scrub(r.text)}")
        result = r.json()
        self.cache.set(cache_key, result, ttl)
        return result
