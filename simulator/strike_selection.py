"""
Which option to watch/trade right now, per the course's strike-selection
rules (see strategy/strategy_rules.md sections 3, 9, 11).

Nifty and Bank Nifty: trade near-ATM (not deep ITM as the earliest topics
said - later topics revise this), except the day before expiry and expiry
morning, when the first ITM strike is used; after 12:00 on expiry day, ATM.

Sensex (section 10): only tradable on expiry day or the day before (Angel's
BSE/BFO option-chain data - see angel_data.py); a narrow one-strike-OTM to
one-strike-ITM window, never wider.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from market_data import OptionChainSnapshot, OptionQuote

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


def select_strike(
    snapshot: OptionChainSnapshot,
    option_type: str,
    now: Optional[time] = None,
) -> Optional[OptionQuote]:
    """option_type: 'CE' or 'PE'. Returns the quote to watch/trade, or None
    if the chain doesn't have a usable candidate right now."""
    now = now or datetime.now().time()
    step = _strike_step(snapshot)
    atm = snapshot.atm_strike()
    expiry_today = is_expiry_day(snapshot)
    day_before = is_day_before_expiry(snapshot)

    if snapshot.symbol == "SENSEX":
        if not (expiry_today or day_before):
            return None  # only trade Sensex on expiry day or the day before (§10)
        # narrow one-strike-OTM to one-strike-ITM window; default to ATM itself
        target = atm
    elif expiry_today and now >= EXPIRY_ATM_SWITCH_TIME:
        target = atm  # ATM / hero-zero window after noon on expiry
    elif expiry_today or day_before:
        # first ITM strike: for a call that's one step below spot, for a put one step above
        target = atm - step if option_type == "CE" else atm + step
    else:
        target = atm  # general near-ATM case

    quote = _find_quote(snapshot, target, option_type)
    if quote is not None:
        return quote
    # Fall back to ATM itself if the computed target strike isn't listed
    # (can happen right at the edge of the fetched chain window).
    return _find_quote(snapshot, atm, option_type)
