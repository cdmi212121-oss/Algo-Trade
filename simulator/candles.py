"""
Builds OHLC candles from our own polled LTP ticks (price_history/*.csv).

These are not official exchange candles - they're built from whatever we
polled Angel One at (every few seconds), so a candle's open/high/low/close
are only as accurate as that polling interval. Fine for a paper-trading
dashboard's visual context; not a substitute for a real tick/quote feed.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

PRICE_HISTORY_DIR = "price_history"


def load_ticks(symbol: str, day: str) -> list[tuple[datetime, float]]:
    path = os.path.join(PRICE_HISTORY_DIR, f"{symbol}_{day}.csv")
    if not os.path.exists(path):
        return []
    ticks = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ticks.append((datetime.fromisoformat(row["timestamp"]), float(row["ltp"])))
    return ticks


def candles_from_ticks(ticks: list[tuple[datetime, float]], interval_minutes: int = 1) -> list[dict]:
    if not ticks:
        return []

    bucket_seconds = interval_minutes * 60
    buckets: dict[int, list[float]] = {}
    for ts, price in ticks:
        bucket_start = int(ts.timestamp()) // bucket_seconds * bucket_seconds
        buckets.setdefault(bucket_start, []).append(price)

    candles = []
    for bucket_start in sorted(buckets):
        prices = buckets[bucket_start]
        candles.append({
            "time": bucket_start,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
        })
    return candles


def build_candles(symbol: str, day: str, interval_minutes: int = 1) -> list[dict]:
    return candles_from_ticks(load_ticks(symbol, day), interval_minutes)
