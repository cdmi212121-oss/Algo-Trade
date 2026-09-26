"""
Live market data via Angel One's SmartAPI - official, authenticated, and not
subject to the anti-bot blocking that killed the free NSE scraping approach.

Credentials are read from environment variables only; never hardcode them
here or paste them into chat/commit history:

    ANGEL_API_KEY       - from the app you register at smartapi.angelone.in
    ANGEL_CLIENT_CODE   - your Angel One client/login ID
    ANGEL_PIN           - your 4-digit MPIN (used for API login, not the
                           full trading password)
    ANGEL_TOTP_SECRET   - the base32 secret shown when you enable TOTP under
                           Angel One profile settings (not a 6-digit code -
                           the underlying secret used to generate them)

Set these in your own shell/session (e.g. `$env:ANGEL_API_KEY = '...'` in
PowerShell) or a local, gitignored .env file - not in any file that gets
shared or committed.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import logzero
import pyotp
import requests
from dotenv import load_dotenv

# Neutralize SmartApi's own SmartConnect.__init__ unguarded filesystem
# write, BEFORE importing SmartConnect - its __init__ unconditionally calls
# logzero.logfile(...) to set up a date-wise log file under
# logs/<date>/app.log, with no constructor parameter to opt out. On
# Render's free tier this has been observed to intermittently hang
# indefinitely instead of failing fast, leaving the whole engine stuck
# with no error and no way for our own timeout wrapper to interrupt it
# (a genuine hang here doesn't yield the GIL the way normal blocking I/O
# does). Patching it to a no-op removes the risky call entirely, rather
# than trying to work around a hang after the fact. This is our own code
# doing the patch, not a change to the installed package (which gets
# reinstalled fresh on every deploy anyway).
logzero.logfile = lambda *args, **kwargs: None

from SmartApi.smartConnect import SmartConnect  # noqa: E402 - must import after the patch above

from ist_clock import now_ist, today_ist
from market_data import OptionChainSnapshot, OptionQuote

load_dotenv()  # loads .env from the project root if present; real env vars still take precedence

INSTRUMENT_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
INSTRUMENT_CACHE_PATH = os.path.join("tools", "cache", "angel_instruments.json")
INSTRUMENT_CACHE_MAX_AGE = timedelta(hours=20)

INDEX_SPOT_TOKEN = {
    "NIFTY": ("NSE", "99926000"),
    "BANKNIFTY": ("NSE", "99926009"),
    "FINNIFTY": ("NSE", "99926037"),
    "SENSEX": ("BSE", "99919000"),
}

# Option-chain exchange segment differs: NSE indices trade options on NFO,
# Sensex (BSE) trades them on BFO.
OPTION_EXCH_SEG = {
    "NIFTY": "NFO",
    "BANKNIFTY": "NFO",
    "FINNIFTY": "NFO",
    "SENSEX": "BFO",
}


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable {name}. See angel_data.py "
            "module docstring for what to set and how to get it."
        )
    return value


class AngelOneClient:
    def __init__(self):
        print("CHECKPOINT: AngelOneClient.__init__ start", flush=True)
        self.api_key = _require_env("ANGEL_API_KEY")
        print("CHECKPOINT: got ANGEL_API_KEY", flush=True)
        self.client_code = _require_env("ANGEL_CLIENT_CODE")
        print("CHECKPOINT: got ANGEL_CLIENT_CODE", flush=True)
        self.pin = _require_env("ANGEL_PIN")
        print("CHECKPOINT: got ANGEL_PIN", flush=True)
        self.totp_secret = _require_env("ANGEL_TOTP_SECRET")
        print("CHECKPOINT: got ANGEL_TOTP_SECRET, calling SmartConnect()", flush=True)

        # SmartConnect.__init__ also unconditionally does
        # os.makedirs(f"logs/{today}", exist_ok=True) BEFORE calling
        # logzero.logfile() (which we've already neutralized above) - that
        # makedirs call is a second, separate candidate for the same kind
        # of intermittent hang on Render's filesystem. Pre-create the
        # directory ourselves with the real os.makedirs first (so it's a
        # cheap no-op either way), then temporarily patch os.makedirs to a
        # genuine no-op for the duration of this one call, restoring the
        # real function immediately afterward - this is scoped narrowly so
        # it can't mask a real missing-directory bug anywhere else in the
        # app.
        today_str = time.strftime("%Y-%m-%d", time.localtime())
        try:
            os.makedirs(os.path.join("logs", today_str), exist_ok=True)
        except Exception:
            pass  # non-fatal - the no-op patch below covers this call regardless
        _real_makedirs = os.makedirs
        os.makedirs = lambda *args, **kwargs: None
        try:
            self.connect = SmartConnect(api_key=self.api_key)
        finally:
            os.makedirs = _real_makedirs
        print("CHECKPOINT: SmartConnect() returned", flush=True)
        self._session = None
        self._instruments: Optional[list[dict]] = None
        print("CHECKPOINT: AngelOneClient.__init__ done", flush=True)

    def login(self) -> None:
        totp_code = pyotp.TOTP(self.totp_secret).now()
        self._session = self.connect.generateSession(self.client_code, self.pin, totp_code)
        if not self._session.get("status"):
            raise RuntimeError(f"Angel One login failed: {self._session}")

    def _ensure_logged_in(self) -> None:
        if self._session is None:
            self.login()

    def _load_instruments(self) -> list[dict]:
        if self._instruments is not None:
            return self._instruments

        os.makedirs(os.path.dirname(INSTRUMENT_CACHE_PATH), exist_ok=True)
        if os.path.exists(INSTRUMENT_CACHE_PATH):
            age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(INSTRUMENT_CACHE_PATH))
            if age < INSTRUMENT_CACHE_MAX_AGE:
                with open(INSTRUMENT_CACHE_PATH, "r") as f:
                    self._instruments = json.load(f)
                    return self._instruments

        resp = requests.get(INSTRUMENT_MASTER_URL, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        with open(INSTRUMENT_CACHE_PATH, "w") as f:
            json.dump(data, f)
        self._instruments = data
        return data

    def _nearest_expiry(self, symbol: str) -> str:
        instruments = self._load_instruments()
        today = today_ist()
        expiries = set()
        for inst in instruments:
            if inst.get("name") == symbol and inst.get("instrumenttype") == "OPTIDX":
                expiries.add(inst["expiry"])

        def parse(exp: str) -> date:
            return datetime.strptime(exp, "%d%b%Y").date()

        future = sorted((e for e in expiries if parse(e) >= today), key=parse)
        if not future:
            raise RuntimeError(f"No upcoming expiries found for {symbol}")
        return future[0]

    def _spot_price(self, symbol: str) -> float:
        return self.get_spot_snapshot([symbol])[symbol]["ltp"]

    def get_spot_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        """Batched LTP + prev-close + change for one or more index symbols
        (NIFTY/BANKNIFTY/FINNIFTY on NSE, SENSEX on BSE), in a single call
        where possible."""
        self._ensure_logged_in()
        tokens_by_exch: dict[str, list[str]] = {}
        token_to_symbol: dict[str, str] = {}
        for symbol in symbols:
            exch, token = INDEX_SPOT_TOKEN[symbol]
            tokens_by_exch.setdefault(exch, []).append(token)
            token_to_symbol[token] = symbol

        result = self.connect.getMarketData("FULL", tokens_by_exch)
        fetched = result.get("data", {}).get("fetched", [])

        snapshot: dict[str, dict] = {}
        for row in fetched:
            symbol = token_to_symbol.get(row["symbolToken"])
            if symbol is None:
                continue
            snapshot[symbol] = {
                "ltp": float(row.get("ltp", 0.0)),
                "open": float(row.get("open", 0.0)),
                "high": float(row.get("high", 0.0)),
                "low": float(row.get("low", 0.0)),
                "prev_close": float(row.get("close", 0.0)),
                "net_change": float(row.get("netChange", 0.0)),
                "pct_change": float(row.get("percentChange", 0.0)),
            }

        missing = set(symbols) - snapshot.keys()
        if missing:
            raise RuntimeError(f"Could not fetch spot snapshot for {missing}: {result}")
        return snapshot

    def get_option_chain(self, symbol: str, strikes_around_atm: int = 10) -> OptionChainSnapshot:
        self._ensure_logged_in()
        symbol = symbol.upper()
        instruments = self._load_instruments()
        expiry = self._nearest_expiry(symbol)
        spot = self._spot_price(symbol)

        chain_instruments = [
            inst for inst in instruments
            if inst.get("name") == symbol
            and inst.get("instrumenttype") == "OPTIDX"
            and inst.get("expiry") == expiry
        ]
        lot_size = int(chain_instruments[0]["lotsize"]) if chain_instruments else 0

        strikes = sorted({float(inst["strike"]) / 100 for inst in chain_instruments})
        atm = min(strikes, key=lambda s: abs(s - spot))
        atm_index = strikes.index(atm)
        window = strikes[max(0, atm_index - strikes_around_atm): atm_index + strikes_around_atm + 1]
        window_set = set(window)

        selected = [inst for inst in chain_instruments if float(inst["strike"]) / 100 in window_set]

        quotes: list[OptionQuote] = []
        token_to_meta = {inst["token"]: inst for inst in selected}
        tokens = list(token_to_meta.keys())

        exch_seg = OPTION_EXCH_SEG[symbol]
        for i in range(0, len(tokens), 50):
            batch = tokens[i:i + 50]
            result = self.connect.getMarketData("FULL", {exch_seg: batch})
            fetched = result.get("data", {}).get("fetched", [])
            for row in fetched:
                meta = token_to_meta.get(row["symbolToken"])
                if meta is None:
                    continue
                option_type = "CE" if meta["symbol"].endswith("CE") else "PE"
                depth = row.get("depth") or {}
                buy_levels = depth.get("buy") or []
                sell_levels = depth.get("sell") or []
                quotes.append(
                    OptionQuote(
                        strike=float(meta["strike"]) / 100,
                        expiry=meta["expiry"],
                        option_type=option_type,
                        ltp=float(row.get("ltp", 0.0)),
                        oi=int(row.get("opnInterest", 0)),
                        change_in_oi=0,
                        volume=int(row.get("tradeVolume", 0)),
                        iv=0.0,
                        bid_price=float(buy_levels[0]["price"]) if buy_levels else 0.0,
                        ask_price=float(sell_levels[0]["price"]) if sell_levels else 0.0,
                        day_high=float(row.get("high", 0.0)),
                        day_low=float(row.get("low", 0.0)),
                        net_change=float(row.get("netChange", 0.0)),
                        pct_change=float(row.get("percentChange", 0.0)),
                    )
                )
            time.sleep(0.34)  # stay under Angel's ~3 req/sec rate limit

        return OptionChainSnapshot(
            symbol=symbol,
            underlying_value=spot,
            timestamp=now_ist(),
            expiries=[expiry],
            quotes=quotes,
            lot_size=lot_size,
        )


class AngelOneClientProxy:
    """Drop-in replacement for AngelOneClient that runs the real client in
    a dedicated child process (see angel_worker.py for why and the wire
    protocol), so a hang inside SmartConnect() can always be recovered from
    by killing that process - something a purely thread-based timeout
    cannot reliably do. Exposes only what's actually used elsewhere
    (engine.py's get_spot_snapshot/get_option_chain calls, app.py's
    client_code read) - anything else added to AngelOneClient later needs a
    matching passthrough method added here too.

    Self-heals during the day too, not just at startup: if a single call
    hangs past CALL_TIMEOUT_S, the worker is killed and a fresh one spawned
    before raising - the engine's own per-tick try/except already logs and
    continues, so the NEXT tick gets a working worker automatically, with no
    manual restart and no Render redeploy needed.
    """

    STARTUP_TIMEOUT_S = 45   # per spawn attempt
    CALL_TIMEOUT_S = 30      # per individual method call once running
    MAX_STARTUP_ATTEMPTS = 6
    RESTART_DELAY_S = 5

    def __init__(self):
        # Known directly from the env var - no need to ask the worker just
        # for this, and it lets app.py's masked client_code display work
        # even while a worker (re)spawn is still in progress.
        self.client_code = _require_env("ANGEL_CLIENT_CODE")
        # "fork" on Linux (Render/production) - a direct OS syscall with no
        # re-exec or re-import of the interpreter, so there's far less that
        # can go wrong in an unusual container environment. "spawn" is the
        # only option on Windows (local dev) at all, and was previously
        # forced everywhere - but on Render, a deployment was observed stuck
        # with the worker never reporting ready OR a startup error even
        # long past every internal timeout, consistent with .start() itself
        # hanging during spawn's re-exec/bootstrap, before our own timeout
        # code ever gets a chance to run. fork's only real documented risk
        # is with a multi-threaded parent (which this is, Flask + the
        # engine-startup thread) - but _worker_main touches no inherited
        # parent state at all (it only imports angel_data fresh and builds
        # a new client), so that risk is low in practice; a fork-related
        # issue would also fail fast (child exits) rather than hang
        # silently, which is strictly easier to recover from via the
        # existing kill-and-retry loop below.
        self._ctx = multiprocessing.get_context("spawn" if sys.platform == "win32" else "fork")
        self._process: Optional[multiprocessing.Process] = None
        self._request_q = None
        self._response_q = None
        self._start_with_retry()

    def _start_with_retry(self) -> None:
        last_error = "unknown"
        for attempt in range(1, self.MAX_STARTUP_ATTEMPTS + 1):
            print(f"CHECKPOINT: AngelOneClient worker spawn attempt {attempt}/{self.MAX_STARTUP_ATTEMPTS}", flush=True)
            ok, detail = self._spawn_and_wait_ready()
            if ok:
                print("CHECKPOINT: AngelOneClient worker ready", flush=True)
                return
            last_error = detail
            print(f"CHECKPOINT: AngelOneClient worker spawn attempt {attempt} failed: {detail}", flush=True)
            self._kill_process()
            if attempt < self.MAX_STARTUP_ATTEMPTS:
                time.sleep(self.RESTART_DELAY_S)
        raise RuntimeError(
            f"AngelOneClient worker process failed to start after "
            f"{self.MAX_STARTUP_ATTEMPTS} attempts: {last_error}"
        )

    def _spawn_and_wait_ready(self) -> tuple[bool, str]:
        self._request_q = self._ctx.Queue()
        self._response_q = self._ctx.Queue()
        from angel_worker import _worker_main
        self._process = self._ctx.Process(
            target=_worker_main, args=(self._request_q, self._response_q), daemon=True,
        )
        self._process.start()
        try:
            status, payload = self._response_q.get(timeout=self.STARTUP_TIMEOUT_S)
        except queue.Empty:
            return False, f"no response within {self.STARTUP_TIMEOUT_S}s (worker stuck or crashed silently)"
        if status == "ready":
            return True, ""
        return False, str(payload)  # "startup_error" - e.g. missing/invalid credentials

    def _kill_process(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
        self._process = None

    def _call(self, method: str, *args, **kwargs):
        if self._process is None or not self._process.is_alive():
            self._start_with_retry()
        self._request_q.put(("call", method, args, kwargs))
        try:
            status, payload = self._response_q.get(timeout=self.CALL_TIMEOUT_S)
        except queue.Empty:
            print(f"CHECKPOINT: AngelOneClient worker call to {method} timed out after "
                  f"{self.CALL_TIMEOUT_S}s - killing and respawning", flush=True)
            self._kill_process()
            self._start_with_retry()
            raise RuntimeError(
                f"AngelOneClient worker call to {method} timed out - a fresh worker "
                "has been started, this will work again next tick"
            )
        if status == "error":
            raise RuntimeError(f"AngelOneClient worker error in {method}: {payload}")
        return payload

    def get_spot_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        return self._call("get_spot_snapshot", symbols)

    def get_option_chain(self, symbol: str, strikes_around_atm: int = 10) -> OptionChainSnapshot:
        return self._call("get_option_chain", symbol, strikes_around_atm=strikes_around_atm)


if __name__ == "__main__":
    client = AngelOneClient()
    snap = client.get_option_chain("NIFTY")
    print(f"{snap.symbol} spot: {snap.underlying_value}  expiry: {snap.nearest_expiry()}  lot_size: {snap.lot_size}")
    atm = snap.atm_strike()
    print(f"ATM strike: {atm}")
    for q in snap.for_expiry():
        if q.strike == atm:
            print(q)
