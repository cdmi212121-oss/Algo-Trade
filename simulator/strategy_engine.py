"""
Entry-signal logic, implemented from strategy/strategy_rules.md (extracted
from the course transcripts). Read that doc for the full rule citations -
this module implements its "Full entry pipeline" section:

  1. A swing tracker on the underlying index's own tick series (candle-based
     zigzag: a swing high/low confirms after 3+ consecutive candles move
     away from it - see swing.py).
  2. A second swing tracker on the specific option's premium tick series
     (the strike currently selected by strike_selection.py).
  3. Strike selection: near-ATM window for Nifty/Bank Nifty, switching to
     the first ITM strike the day before/morning of expiry, ATM after noon
     on expiry day; a narrow one-strike window for Sensex, expiry-adjacent
     days only.
  4. Entry trigger: the underlying breaks its last confirmed swing AND the
     option premium independently breaks its own last confirmed swing, in
     the same direction, AND the session-timing gate allows it right now.
  5. Stop loss: the smaller of a fixed point value (by instrument) and the
     structural distance to the premium's opposite last swing - the
     "hybrid" method the transcript explicitly endorses as valid.
  6. Target/exit management: `manage_position` implements the transcript's
     primary momentum-based scale-out (book ~75% of the position once
     unrealized profit clears a minimum and the premium prints a
     consolidation candle, move the remainder's SL to breakeven and let it
     ride to the fixed target as a backstop). Called by engine.py once per
     tick for each open position.

One thing from the source material is deliberately NOT implemented here,
noted rather than silently dropped: the 15-minute higher-timeframe
"directional bias" check (checked 4x/day in the transcript) is documented
as context, not a hard entry filter, and the doc's own final pipeline
doesn't list it as a required AND condition - so it's omitted rather than
guessed at. The opening-15-minutes gap-up/gap-down zone-touch special case
(Topic 7) also isn't separately coded; the generic swing tracker naturally
can't confirm any swing until enough candles exist anyway, which covers
most of the same ground without hand-coding the zone-specific scenarios.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from candles import build_candles, candles_from_ticks
from market_data import OptionChainSnapshot, instrument_key
from paper_broker import Side
from strike_selection import is_expiry_day, select_strike
from swing import SwingTracker, candle_bias

CANDLE_SWITCH_TIME = time(10, 30)
SIDEWAYS_WINDOW = (time(11, 30), time(13, 30))
SLOW_DIRECTIONAL_WINDOW = (time(13, 30), time(15, 30))
MAX_PREMIUM_TICKS = 2000  # per watched instrument per day - plenty for intraday candles

MIN_FAVORABLE_MOVE = 15.0  # points of unrealized profit before scale-out can trigger (§8)
SCALE_OUT_FRACTION = 0.75  # book ~70-80% on the first consolidation signal (§8)


@dataclass
class Signal:
    symbol: str  # instrument_key(symbol, quote) - engine.py prefixes the underlying symbol itself
    side: Side
    entry_price: float
    stop_loss: float
    target: float
    trail_sl_step: float | None = None
    size_multiplier: float = 1.0  # <1.0 for the "small quantity" windows in the rules
    reason: str = ""


def _sl_target_points(symbol: str, days_to_expiry: int) -> tuple[float, float]:
    """(stop_loss_points, target_points) on the option premium, by
    instrument. See strategy_rules.md §8/§10/§11 - Bank Nifty's SL widens well
    outside its last week before (monthly) expiry; Nifty stays weekly-scale;
    Sensex uses its own fixed ladder (only traded near expiry - see
    strike_selection.select_strike)."""
    if symbol == "NIFTY":
        return 30.0, 60.0
    if symbol == "BANKNIFTY":
        return (30.0, 60.0) if days_to_expiry <= 7 else (42.0, 84.0)
    if symbol == "SENSEX":
        return 40.0, 30.0  # first target tier of 30/60/90; scale-out logic handles the rest
    return 30.0, 60.0


def candle_interval(now: time) -> int:
    """1-min candles before 10:30, 3-min after (§2) - shared with engine.py
    so open-position management uses the same candle sizing as entries."""
    return 1 if now < CANDLE_SWITCH_TIME else 3


class StrategyEngine:
    def __init__(self):
        self._underlying_trackers: dict[str, SwingTracker] = {}
        self._premium_trackers: dict[tuple[str, str], SwingTracker] = {}
        self._premium_ticks: dict[tuple[str, str], deque] = {}
        self._scaled_out: set[str] = set()  # position keys already scaled out once

    def clear_position_state(self, position_key: str) -> None:
        """Call once a position is fully closed, so its key can be reused
        (e.g. re-entering the same strike later the same day) cleanly."""
        self._scaled_out.discard(position_key)

    def manage_position(self, position_key: str, position, premium_candles: list[dict],
                          current_ltp: float) -> Optional[dict]:
        """Momentum-based scale-out decision for an already-open position
        (§8): once unrealized profit clears MIN_FAVORABLE_MOVE, book most of
        the quantity on the first consolidation (doji-ish) candle and let
        the rest ride to breakeven-or-better. Fires at most once per
        position - returns None on every other tick for that position."""
        if position_key in self._scaled_out:
            return None

        favorable_move = position.unrealized_pnl(current_ltp) / position.qty if position.qty else 0.0
        if favorable_move < MIN_FAVORABLE_MOVE:
            return None
        if not premium_candles:
            return None

        if candle_bias(premium_candles[-1]) == "doji":
            self._scaled_out.add(position_key)
            return {"action": "partial_exit", "fraction": SCALE_OUT_FRACTION}
        return None

    def _session_gate(self, now: time, expiry_today: bool) -> tuple[bool, float]:
        """(allowed, size_multiplier)."""
        start, end = SIDEWAYS_WINDOW
        if start <= now < end:
            return (True, 0.5) if expiry_today else (False, 0.0)
        start, end = SLOW_DIRECTIONAL_WINDOW
        if start <= now < end:
            return True, 0.5
        return True, 1.0

    def _watch_premium(self, symbol: str, option_type: str, quote, now: datetime, interval: int) -> SwingTracker:
        key = (symbol, option_type)
        ticks = self._premium_ticks.setdefault(key, deque(maxlen=MAX_PREMIUM_TICKS))
        ticks.append((now, quote.ltp))
        tracker = self._premium_trackers.setdefault(key, SwingTracker())
        tracker.load_history(candles_from_ticks(list(ticks), interval))
        return tracker

    def on_tick(self, snapshot: OptionChainSnapshot) -> list[Signal]:
        symbol = snapshot.symbol
        now_dt = datetime.now()
        now = now_dt.time()
        interval = candle_interval(now)
        expiry_today = is_expiry_day(snapshot)

        allowed, size_multiplier = self._session_gate(now, expiry_today)
        if not allowed:
            return []

        underlying_candles = build_candles(symbol, date.today().isoformat(), interval)
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

        expiry_date = None
        try:
            expiry_date = datetime.strptime(snapshot.nearest_expiry(), "%d%b%Y").date()
        except ValueError:
            pass
        days_to_expiry = (expiry_date - date.today()).days if expiry_date else 7

        sl_points, target_points = _sl_target_points(symbol, days_to_expiry)
        opposite_swing = premium_tracker.last_swing_low if option_type == "CE" else premium_tracker.last_swing_high
        if opposite_swing is not None:
            structural = abs(quote.ltp - opposite_swing)
            if structural > 0:
                sl_points = min(structural, sl_points)

        stop_loss = quote.ltp - sl_points
        target = quote.ltp + target_points
        if stop_loss <= 0:
            return []  # degenerate SL (premium too small) - skip rather than risk it all

        key = instrument_key(symbol, quote)
        return [Signal(
            symbol=key,
            side=Side.BUY,
            entry_price=quote.ltp,
            stop_loss=stop_loss,
            target=target,
            size_multiplier=size_multiplier,
            reason=f"underlying+premium {underlying_break}-break ({option_type})",
        )]
