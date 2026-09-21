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
import os
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pyotp
import requests
from dotenv import load_dotenv
from SmartApi.smartConnect import SmartConnect

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
        self.api_key = _require_env("ANGEL_API_KEY")
        self.client_code = _require_env("ANGEL_CLIENT_CODE")
        self.pin = _require_env("ANGEL_PIN")
        self.totp_secret = _require_env("ANGEL_TOTP_SECRET")

        self.connect = SmartConnect(api_key=self.api_key)
        self._session = None
        self._instruments: Optional[list[dict]] = None

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
        today = date.today()
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
            timestamp=datetime.now(),
            expiries=[expiry],
            quotes=quotes,
            lot_size=lot_size,
        )


if __name__ == "__main__":
    client = AngelOneClient()
    snap = client.get_option_chain("NIFTY")
    print(f"{snap.symbol} spot: {snap.underlying_value}  expiry: {snap.nearest_expiry()}  lot_size: {snap.lot_size}")
    atm = snap.atm_strike()
    print(f"ATM strike: {atm}")
    for q in snap.for_expiry():
        if q.strike == atm:
            print(q)
