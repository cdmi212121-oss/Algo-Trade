"""Shared market data shapes used by any data feed client (nse_data.py's
scraper is dead - NSE now blocks it at the API level - and angel_data.py is
the live client, but both would produce these same shapes)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class OptionQuote:
    strike: float
    expiry: str
    option_type: str  # "CE" or "PE"
    ltp: float
    oi: int
    change_in_oi: int
    volume: int
    iv: float
    bid_price: float
    ask_price: float
    day_high: float = 0.0
    day_low: float = 0.0
    net_change: float = 0.0
    pct_change: float = 0.0


@dataclass
class OptionChainSnapshot:
    symbol: str
    underlying_value: float
    timestamp: datetime
    expiries: list[str]
    quotes: list[OptionQuote]
    lot_size: int

    def nearest_expiry(self) -> str:
        return self.expiries[0]

    def for_expiry(self, expiry: Optional[str] = None) -> list[OptionQuote]:
        return [q for q in self.quotes if q.expiry == (expiry or self.nearest_expiry())]

    def atm_strike(self, expiry: Optional[str] = None) -> float:
        strikes = sorted({q.strike for q in self.for_expiry(expiry)})
        if not strikes:
            raise ValueError(f"No strikes found for expiry {expiry}")
        return min(strikes, key=lambda s: abs(s - self.underlying_value))


def instrument_key(symbol: str, quote: OptionQuote) -> str:
    """Internal-only unique key for a paper position/instrument. Not a real
    NSE/broker trading symbol - just needs to be stable and collision-free."""
    return f"{symbol}_{quote.expiry}_{int(quote.strike)}_{quote.option_type}"
