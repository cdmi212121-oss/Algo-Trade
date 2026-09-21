"""
Standalone options-scalping algo — entry/exit logic only, no data feed, no
broker, no dashboard. Portable: stdlib only, drop this one file into any
Python project.

Rules implemented (full citations in strategy_rules.md, in the original
project this was extracted from): swing detection by candle count, strike
selection by moneyness, dual underlying+premium confirmation, session-time
gating, hybrid fixed/structural stop-loss, momentum-based scale-out target.
This is a mechanical translation of a specific course's rules, not a
guarantee of profitability - test thoroughly in paper trading before ever
considering it for real money, and note the "NOT IMPLEMENTED" items in the
module docstring at the bottom of this file before relying on it.

============================== HOW TO USE THIS ==============================

You bring: a live market data feed and an order-execution/paper-trading
layer. This file brings: the decision logic. Nothing in here places an
order or touches money - it only returns *signals* (recommendations) that
your own code acts on.

Per polling tick, for each index you're scanning:

    from algo import StrategyEngine, OptionChainSnapshot, OptionQuote

    engine = StrategyEngine()

    snapshot = OptionChainSnapshot(
        symbol="NIFTY",                  # or "BANKNIFTY", "SENSEX"
        underlying_value=23346.4,        # current spot/futures price
        timestamp=datetime.now(),
        expiries=["25SEP2026"],          # nearest expiry first, format DDMmmYYYY
        lot_size=65,                     # current lot size for this symbol
        quotes=[
            OptionQuote(strike=23300.0, expiry="25SEP2026", option_type="CE",
                        ltp=95.5, oi=0, change_in_oi=0, volume=0, iv=0.0,
                        bid_price=95.0, ask_price=96.0),
            # ... one OptionQuote per strike/side you have data for, ideally
            # a window of a few strikes either side of ATM
        ],
    )

    underlying_candles = your_own_candle_builder(...)  # see candles_from_ticks() below -
                                                        # 1-min candles before 10:30 IST, 3-min after (see candle_interval())

    signals = engine.on_tick(snapshot, underlying_candles)
    for sig in signals:
        # sig.symbol is a stable instrument key (not a real broker trading
        # symbol) - map it to whatever your own system needs to place a
        # BUY order at sig.entry_price with sig.stop_loss / sig.target.
        ...

For every position you currently have OPEN (regardless of how it was
opened), call this every tick too:

    action = engine.manage_position(
        position_key=sig.symbol,   # whatever key you used to track this position
        side="BUY",
        entry_price=95.5,
        premium_candles=your_own_candle_builder_for_this_specific_option(...),
        current_ltp=current_premium,
    )
    if action and action["action"] == "partial_exit":
        # close action["fraction"] (e.g. 0.75) of the position's quantity
        # at current_ltp, then move the remaining quantity's stop-loss to
        # entry_price (breakeven) in your own position tracking.
        ...

`candles_from_ticks()` is included below so you can build the OHLC candles
this needs from whatever raw tick/LTP stream your feed gives you - feed it
a list of (datetime, price) tuples.

When a position closes (by any means), call
`engine.clear_position_state(position_key)` so the key can be reused.
==============================================================================
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional


# ============================================================================
# Market data shapes - construct these from your own feed each tick.
# ============================================================================

@dataclass
class OptionQuote:
    strike: float
    expiry: str          # format: "25SEP2026" (day, 3-letter month, year)
    option_type: str     # "CE" or "PE"
    ltp: float
    oi: int
    change_in_oi: int
    volume: int
    iv: float
    bid_price: float
    ask_price: float


@dataclass
class OptionChainSnapshot:
    symbol: str                # "NIFTY" / "BANKNIFTY" / "SENSEX"
    underlying_value: float
    timestamp: datetime
    expiries: list[str]        # nearest expiry first
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
    """Stable, unique key for an instrument - not a real broker trading
    symbol, just something to track a position by."""
    return f"{symbol}_{quote.expiry}_{int(quote.strike)}_{quote.option_type}"


# ============================================================================
# Candle building - feed this your own raw ticks.
# ============================================================================

def candles_from_ticks(ticks: list[tuple[datetime, float]], interval_minutes: int = 1) -> list[dict]:
    """ticks: list of (timestamp, price), any order. Returns OHLC candles
    (dicts with time/open/high/low/close) bucketed by interval_minutes,
    oldest first. These aren't official exchange candles - they're only as
    accurate as how often you sampled ticks."""
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


# ============================================================================
# Swing detection - a swing high/low confirms once 3 consecutive candles
# move away from it in the opposite direction (candle-count based, not a
# fixed point threshold).
# ============================================================================

CONFIRM_CANDLES = 3


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
        """Reset and replay a full candle history (simplest way to stay
        correct if you rebuild candles from scratch each poll)."""
        self.__init__()
        for candle in candles:
            self.feed(candle)

    def feed(self, candle: dict) -> None:
        if self.direction is None:
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
        """'up' if latest_price broke the last confirmed swing high, 'down'
        if it broke the last confirmed swing low, else None."""
        if self.last_swing_high is not None and latest_price > self.last_swing_high:
            return "up"
        if self.last_swing_low is not None and latest_price < self.last_swing_low:
            return "down"
        return None


# ============================================================================
# Strike selection - which option to watch/trade right now.
# ============================================================================

EXPIRY_ATM_SWITCH_TIME = time(12, 0)


def _parse_expiry(expiry: str) -> date:
    return datetime.strptime(expiry, "%d%b%Y").date()


def _strike_step(snapshot: OptionChainSnapshot) -> float:
    strikes = sorted({q.strike for q in snapshot.quotes})
    if len(strikes) < 2:
        return 50.0
    return min(b - a for a, b in zip(strikes, strikes[1:]) if b > a)


def is_expiry_day(snapshot: OptionChainSnapshot, today: Optional[date] = None) -> bool:
    today = today or date.today()
    return _parse_expiry(snapshot.nearest_expiry()) == today


def is_day_before_expiry(snapshot: OptionChainSnapshot, today: Optional[date] = None) -> bool:
    today = today or date.today()
    return (_parse_expiry(snapshot.nearest_expiry()) - today).days == 1


def _find_quote(snapshot: OptionChainSnapshot, strike: float, option_type: str) -> Optional[OptionQuote]:
    return next((q for q in snapshot.quotes if q.strike == strike and q.option_type == option_type), None)


def select_strike(snapshot: OptionChainSnapshot, option_type: str, now: Optional[time] = None) -> Optional[OptionQuote]:
    """option_type: 'CE' or 'PE'. Nifty/Bank Nifty trade near-ATM generally,
    switching to the first ITM strike the day before/morning of expiry and
    ATM after noon on expiry day. Sensex only trades on expiry day or the
    day before, in a narrow one-strike window."""
    now = now or datetime.now().time()
    step = _strike_step(snapshot)
    atm = snapshot.atm_strike()
    expiry_today = is_expiry_day(snapshot)
    day_before = is_day_before_expiry(snapshot)

    if snapshot.symbol == "SENSEX":
        if not (expiry_today or day_before):
            return None
        target = atm
    elif expiry_today and now >= EXPIRY_ATM_SWITCH_TIME:
        target = atm
    elif expiry_today or day_before:
        target = atm - step if option_type == "CE" else atm + step
    else:
        target = atm

    quote = _find_quote(snapshot, target, option_type)
    if quote is not None:
        return quote
    return _find_quote(snapshot, atm, option_type)


# ============================================================================
# The algo itself.
# ============================================================================

CANDLE_SWITCH_TIME = time(10, 30)
SIDEWAYS_WINDOW = (time(11, 30), time(13, 30))
SLOW_DIRECTIONAL_WINDOW = (time(13, 30), time(15, 30))
MAX_PREMIUM_TICKS = 2000

MIN_FAVORABLE_MOVE = 15.0    # points of unrealized profit before scale-out can trigger
SCALE_OUT_FRACTION = 0.75    # book ~70-80% on the first consolidation signal


def candle_interval(now: time) -> int:
    """1-min candles before 10:30 IST, 3-min after."""
    return 1 if now < CANDLE_SWITCH_TIME else 3


def _sl_target_points(symbol: str, days_to_expiry: int) -> tuple[float, float]:
    """(stop_loss_points, target_points) on the option premium, by instrument."""
    if symbol == "NIFTY":
        return 30.0, 60.0
    if symbol == "BANKNIFTY":
        return (30.0, 60.0) if days_to_expiry <= 7 else (42.0, 84.0)
    if symbol == "SENSEX":
        return 40.0, 30.0
    return 30.0, 60.0


@dataclass
class Signal:
    symbol: str                          # instrument_key(underlying_symbol, quote)
    side: str = "BUY"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    size_multiplier: float = 1.0         # <1.0 during the "small quantity" windows
    reason: str = ""


class StrategyEngine:
    def __init__(self):
        self._underlying_trackers: dict[str, SwingTracker] = {}
        self._premium_trackers: dict[tuple[str, str], SwingTracker] = {}
        self._premium_ticks: dict[tuple[str, str], deque] = {}
        self._scaled_out: set[str] = set()

    def _session_gate(self, now: time, expiry_today: bool) -> tuple[bool, float]:
        start, end = SIDEWAYS_WINDOW
        if start <= now < end:
            return (True, 0.5) if expiry_today else (False, 0.0)
        start, end = SLOW_DIRECTIONAL_WINDOW
        if start <= now < end:
            return True, 0.5
        return True, 1.0

    def _watch_premium(self, symbol: str, option_type: str, quote: OptionQuote, now: datetime, interval: int) -> SwingTracker:
        key = (symbol, option_type)
        ticks = self._premium_ticks.setdefault(key, deque(maxlen=MAX_PREMIUM_TICKS))
        ticks.append((now, quote.ltp))
        tracker = self._premium_trackers.setdefault(key, SwingTracker())
        tracker.load_history(candles_from_ticks(list(ticks), interval))
        return tracker

    def on_tick(self, snapshot: OptionChainSnapshot, underlying_candles: list[dict]) -> list[Signal]:
        """Call every poll, per symbol. underlying_candles: today's OHLC
        candles for the underlying so far, oldest first, at the interval
        given by candle_interval(now) - build them with candles_from_ticks()
        from your own tick history."""
        symbol = snapshot.symbol
        now_dt = datetime.now()
        now = now_dt.time()
        interval = candle_interval(now)
        expiry_today = is_expiry_day(snapshot)

        allowed, size_multiplier = self._session_gate(now, expiry_today)
        if not allowed:
            return []

        if len(underlying_candles) < 4:
            return []  # not enough history yet to have confirmed any swing today

        underlying_tracker = self._underlying_trackers.setdefault(symbol, SwingTracker())
        underlying_tracker.load_history(underlying_candles)

        underlying_break = underlying_tracker.check_break(snapshot.underlying_value)
        if underlying_break is None:
            return []

        option_type = "CE" if underlying_break == "up" else "PE"
        quote = select_strike(snapshot, option_type, now)
        if quote is None:
            return []

        premium_tracker = self._watch_premium(symbol, option_type, quote, now_dt, interval)
        premium_break = premium_tracker.check_break(quote.ltp)
        if premium_break != underlying_break:
            return []  # underlying moved but the premium hasn't confirmed - no trade

        try:
            expiry_date = datetime.strptime(snapshot.nearest_expiry(), "%d%b%Y").date()
            days_to_expiry = (expiry_date - date.today()).days
        except ValueError:
            days_to_expiry = 7

        sl_points, target_points = _sl_target_points(symbol, days_to_expiry)
        opposite_swing = premium_tracker.last_swing_low if option_type == "CE" else premium_tracker.last_swing_high
        if opposite_swing is not None:
            structural = abs(quote.ltp - opposite_swing)
            if structural > 0:
                sl_points = min(structural, sl_points)

        stop_loss = quote.ltp - sl_points
        target = quote.ltp + target_points
        if stop_loss <= 0:
            return []

        key = instrument_key(symbol, quote)
        return [Signal(
            symbol=key,
            side="BUY",
            entry_price=quote.ltp,
            stop_loss=stop_loss,
            target=target,
            size_multiplier=size_multiplier,
            reason=f"underlying+premium {underlying_break}-break ({option_type})",
        )]

    def manage_position(self, position_key: str, side: str, entry_price: float,
                          premium_candles: list[dict], current_ltp: float) -> Optional[dict]:
        """Call every tick for each position you have open. Returns
        {"action": "partial_exit", "fraction": 0.75} at most once per
        position - book that fraction of the quantity at current_ltp, move
        the remainder's stop-loss to entry_price (breakeven), and ignore
        further calls for this key (it won't fire twice)."""
        if position_key in self._scaled_out:
            return None

        direction = 1 if side.upper() == "BUY" else -1
        favorable_move = direction * (current_ltp - entry_price)
        if favorable_move < MIN_FAVORABLE_MOVE:
            return None
        if not premium_candles:
            return None

        if candle_bias(premium_candles[-1]) == "doji":
            self._scaled_out.add(position_key)
            return {"action": "partial_exit", "fraction": SCALE_OUT_FRACTION}
        return None

    def clear_position_state(self, position_key: str) -> None:
        """Call once a position is fully closed, so the key can be reused."""
        self._scaled_out.discard(position_key)


"""
NOT IMPLEMENTED (either the source material for it doesn't exist yet, or
it's a deliberate v1 simplification - see the originating project's
strategy_rules.md for full detail):

  - Trailing SL beyond the fixed/structural hybrid above; risk-management
    updates; sideways-market handling detail; trend-identification detail.
    These come from later topics in the source course that hadn't been
    transcribed when this was written.
  - The 15-minute higher-timeframe "directional bias" check is not a hard
    filter here (documented as context in the source material, not a
    required condition).
  - The opening-15-minutes gap-up/gap-down special case isn't separately
    coded - the swing tracker naturally can't confirm anything until enough
    candles exist anyway.
  - Strike selection doesn't check for premium data gaps or CE/PE premium
    matching - it picks by strike distance from ATM only.
  - This has not been validated against a real live trading session.
"""
