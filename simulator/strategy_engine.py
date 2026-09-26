"""
Entry-signal logic, implemented from strategy/strategy_rules.md (extracted
from the course transcripts). Read that doc for the full rule citations -
this module implements its "Full entry pipeline" section:

  1. A swing tracker on the underlying index's own tick series (candle-based
     zigzag: a swing high/low confirms after 3+ consecutive candles move
     away from it - see swing.py), at a single candle size by clock time
     per strategy_rules.md §2: 1-minute before 10:30, 3-minute after (see
     candle_interval()). An earlier version of this file scanned multiple
     candle sizes at once per a since-reverted user instruction - reverted
     back to the literal single-timeframe-by-clock-time rule on explicit
     instruction to follow the course strategy exactly, not go outside it.
  2. A second swing tracker on the specific option's premium tick series
     (the strike currently selected by strike_selection.py), at that same
     candle size.
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

The 15-minute higher-timeframe "directional bias" check (§2) IS implemented
(see _maybe_update_bias/BIAS_CHECK_TIMES): 4x/day, at 11:30/12:30/13:30/14:30,
it reads the 15-min chart's current swing direction and uses it as a filter
- an entry whose direction contradicts the standing 15-min bias is skipped.
This matches the transcript's own framing exactly: bias is a context filter
on top of the 1-min/3-min entry trigger, not a replacement entry timeframe
and not a required AND condition before the first checkpoint of the day.

The opening-15-minutes gap-up/gap-down zone-touch special case (Topic 7)
still isn't separately coded; the generic swing tracker naturally can't
confirm any swing until enough candles exist anyway, which covers most of
the same ground without hand-coding the zone-specific scenarios.
"""

from __future__ import annotations

import csv
import os
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from candles import build_candles, candles_from_ticks
from ist_clock import now_ist, today_ist
from market_data import OptionChainSnapshot, instrument_key
from paper_broker import Side
from strike_selection import is_expiry_day, select_strike
from swing import SwingTracker, candle_bias

PREMIUM_HISTORY_DIR = "price_history"

CANDLE_SWITCH_TIME = time(10, 30)
SLOW_DIRECTIONAL_WINDOW = (time(13, 30), time(15, 30))
MAX_PREMIUM_TICKS = 2000  # per watched instrument per day - plenty for intraday candles

# 15-minute directional-bias context check (§2): re-evaluated 4x/day at
# these times, using the 15-min chart's current swing-leg direction as a
# call-lean/put-lean bias filter on top of the 1-min/3-min entry trigger.
BIAS_CHECK_TIMES = [time(11, 30), time(12, 30), time(13, 30), time(14, 30)]
BIAS_CANDLE_INTERVAL = 15

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


def _premium_log_path(symbol: str, option_type: str, day: str) -> str:
    return os.path.join(PREMIUM_HISTORY_DIR, f"PREMIUM_{symbol}_{option_type}_{day}.csv")


def _load_premium_ticks(symbol: str, option_type: str, day: str) -> list[tuple[datetime, float]]:
    """Reload today's premium ticks from disk - so a server restart doesn't
    lose the option premium's swing history the way it would if this only
    lived in memory (the underlying index's history already survives
    restarts this same way, via candles.load_ticks/price_history/*.csv)."""
    path = _premium_log_path(symbol, option_type, day)
    if not os.path.exists(path):
        return []
    ticks = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ticks.append((datetime.fromisoformat(row["timestamp"]), float(row["ltp"])))
    return ticks


def _append_premium_tick(symbol: str, option_type: str, day: str, ts: datetime, ltp: float) -> None:
    os.makedirs(PREMIUM_HISTORY_DIR, exist_ok=True)
    path = _premium_log_path(symbol, option_type, day)
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "ltp"])
        writer.writerow([ts.isoformat(timespec="seconds"), ltp])


class StrategyEngine:
    def __init__(self):
        self._underlying_trackers: dict[tuple[str, int], SwingTracker] = {}
        self._premium_trackers: dict[tuple[str, str, int], SwingTracker] = {}
        # Raw premium ticks are shared across every interval (candle size is
        # only a bucketing choice made afterwards) and persisted to disk -
        # see _load_premium_ticks/_append_premium_tick - so a restart
        # reloads today's history instead of starting empty.
        self._premium_ticks: dict[tuple[str, str], deque] = {}
        self._scaled_out: set[str] = set()  # position keys already scaled out once
        self._directional_bias: dict[str, Optional[str]] = {}  # per symbol: "up"/"down"/None
        self._bias_checked_today: dict[str, set] = {}  # per symbol: which of today's 4 checkpoints ran
        self._bias_day: Optional[date] = None

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

    def _maybe_update_bias(self, symbol: str, now: time) -> None:
        """Re-evaluate the 15-min directional bias once we cross each of
        today's 4 checkpoints (§2), reading the 15-min chart's current
        swing-leg direction. Resets at day-rollover so yesterday's bias
        never leaks into today."""
        today = today_ist()
        if self._bias_day != today:
            self._bias_day = today
            self._directional_bias = {}
            self._bias_checked_today = {}

        checked = self._bias_checked_today.setdefault(symbol, set())
        for checkpoint in BIAS_CHECK_TIMES:
            if now >= checkpoint and checkpoint not in checked:
                candles_15m = build_candles(symbol, today.isoformat(), BIAS_CANDLE_INTERVAL)
                if len(candles_15m) >= 4:
                    bias_tracker = SwingTracker()
                    bias_tracker.load_history(candles_15m)
                    self._directional_bias[symbol] = bias_tracker.direction
                checked.add(checkpoint)

    def _session_gate(self, now: time, expiry_today: bool) -> tuple[bool, float]:
        """(allowed, size_multiplier).

        The course's §2 "sideways market, avoid trading" 11:30-13:30 block
        has been explicitly removed per user instruction (2026-09-25) - the
        user does not believe the market goes sideways in that window and
        wants it treated as normal trending time, full size, no exception.
        This is a deliberate deviation from the literal course rule, not a
        bug fix - kept as a comment so it's not mistaken for one later.
        """
        start, end = SLOW_DIRECTIONAL_WINDOW
        if start <= now < end:
            return True, 0.5
        return True, 1.0

    def _watch_premium(self, symbol: str, option_type: str, quote, now: datetime, interval: int) -> SwingTracker:
        tick_key = (symbol, option_type)
        day = today_ist().isoformat()
        ticks = self._premium_ticks.get(tick_key)
        if ticks is None:
            # Cold start for this instrument today (fresh process, or first
            # time this option_type came up) - reload persisted history
            # before adding today's fresh tick, instead of starting empty.
            ticks = deque(maxlen=MAX_PREMIUM_TICKS)
            for ts, ltp in _load_premium_ticks(symbol, option_type, day):
                ticks.append((ts, ltp))
            self._premium_ticks[tick_key] = ticks

        ticks.append((now, quote.ltp))
        _append_premium_tick(symbol, option_type, day, now, quote.ltp)

        tracker = self._premium_trackers.setdefault((symbol, option_type, interval), SwingTracker())
        tracker.load_history(candles_from_ticks(list(ticks), interval))
        return tracker

    def on_tick(self, snapshot: OptionChainSnapshot) -> list[Signal]:
        symbol = snapshot.symbol
        now_dt = now_ist()
        now = now_dt.time()
        interval = candle_interval(now)  # 1-min before 10:30, 3-min after - §2, literal
        expiry_today = is_expiry_day(snapshot)

        allowed, size_multiplier = self._session_gate(now, expiry_today)
        if not allowed:
            return []

        self._maybe_update_bias(symbol, now)

        underlying_candles = build_candles(symbol, today_ist().isoformat(), interval)
        if len(underlying_candles) < 4:
            return []  # not enough history yet to have confirmed any swing today

        underlying_tracker = self._underlying_trackers.setdefault((symbol, interval), SwingTracker())
        underlying_tracker.load_history(underlying_candles)

        underlying_break = underlying_tracker.check_break(snapshot.underlying_value)
        if underlying_break is None:
            return []

        bias = self._directional_bias.get(symbol)
        if bias is not None and bias != underlying_break:
            return []  # contradicts the standing 15-min directional bias (§2) - skip

        option_type = "CE" if underlying_break == "up" else "PE"
        quote = select_strike(snapshot, option_type, now)
        if quote is None:
            return []

        premium_tracker = self._watch_premium(symbol, option_type, quote, now_dt, interval)
        premium_break = premium_tracker.check_break(quote.ltp)
        # We always BUY the option (CE or PE) and profit when ITS OWN premium rises,
        # so confirmation is always a break of the premium's own recent HIGH - never
        # a literal match against underlying_break's "up"/"down" label (a PE's premium
        # rises when the underlying falls, so that label would never match otherwise).
        if premium_break != "up":
            return []  # underlying moved but the premium hasn't confirmed - no trade

        expiry_date = None
        try:
            expiry_date = datetime.strptime(snapshot.nearest_expiry(), "%d%b%Y").date()
        except ValueError:
            pass
        days_to_expiry = (expiry_date - today_ist()).days if expiry_date else 7

        sl_points, target_points = _sl_target_points(symbol, days_to_expiry)
        # Premium confirmation is always an "up" break now (see above), so the
        # structural reference is always the swing low just before that breakout leg.
        opposite_swing = premium_tracker.last_swing_low
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
