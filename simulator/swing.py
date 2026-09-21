"""
Candle-based zigzag swing tracker.

Per the course rules: a swing high/low is confirmed once a run of
consecutive candles moves away from it in the opposite direction - not a
fixed point threshold. Candles are the dict shape produced by candles.py
({"time", "open", "high", "low", "close"}).
"""

from __future__ import annotations

from typing import Optional

CONFIRM_CANDLES = 3  # consecutive opposite-direction candles to confirm a swing


def candle_bias(candle: dict) -> str:
    """'green', 'red', or 'doji' by body size relative to the candle's range."""
    body = abs(candle["close"] - candle["open"])
    rng = candle["high"] - candle["low"]
    if rng == 0 or body < 0.1 * rng:
        return "doji"
    return "green" if candle["close"] > candle["open"] else "red"


class SwingTracker:
    """Feed candles one at a time (oldest first). Tracks the current leg
    direction and the last *confirmed* swing high/low."""

    def __init__(self):
        self.direction: Optional[str] = None  # "up" or "down"
        self.extreme: Optional[float] = None
        self.opposite_count = 0
        self.last_swing_high: Optional[float] = None
        self.last_swing_low: Optional[float] = None

    def load_history(self, candles: list[dict]) -> None:
        """Reset and replay a full candle history (used since we rebuild
        candles from tick CSVs each poll rather than streaming)."""
        self.__init__()
        for candle in candles:
            self.feed(candle)

    def feed(self, candle: dict) -> None:
        if self.direction is None:
            # Bootstrap: assume an up-leg starting at this candle's low.
            self.direction = "up"
            self.extreme = candle["high"]
            self.opposite_count = 0
            return

        bias = candle_bias(candle)

        if self.direction == "up":
            if candle["high"] > self.extreme:
                self.extreme = candle["high"]
                self.opposite_count = 0
            elif bias == "red":
                self.opposite_count += 1
                if self.opposite_count >= CONFIRM_CANDLES:
                    self.last_swing_high = self.extreme
                    self.direction = "down"
                    self.extreme = candle["low"]
                    self.opposite_count = 0
            elif bias == "green":
                self.opposite_count = 0
            # doji: neither advances nor resets the count

        else:  # direction == "down"
            if candle["low"] < self.extreme:
                self.extreme = candle["low"]
                self.opposite_count = 0
            elif bias == "green":
                self.opposite_count += 1
                if self.opposite_count >= CONFIRM_CANDLES:
                    self.last_swing_low = self.extreme
                    self.direction = "up"
                    self.extreme = candle["high"]
                    self.opposite_count = 0
            elif bias == "red":
                self.opposite_count = 0

    def check_break(self, latest_price: float) -> Optional[str]:
        """Return 'up' if latest_price has broken the last confirmed swing
        high, 'down' if it's broken the last confirmed swing low, else None."""
        if self.last_swing_high is not None and latest_price > self.last_swing_high:
            return "up"
        if self.last_swing_low is not None and latest_price < self.last_swing_low:
            return "down"
        return None
