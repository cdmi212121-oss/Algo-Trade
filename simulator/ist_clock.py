"""
Centralized IST (Asia/Kolkata) clock helpers.

Every trading-hours/session/day-boundary decision in this app (market
open/close, candle-size switch time, expiry-day checks, daily risk
rollover, log file naming) must use these, not bare datetime.now()/
date.today(). Those return the HOST MACHINE's own system clock civil
time, which happens to be IST on a local Windows dev machine but is UTC
on Render's cloud containers (and most cloud hosts, by default). Without
this, every comparison against an IST-based threshold like time(9, 15)
silently uses the wrong civil time on any host that isn't already set to
IST - e.g. 9:16 AM IST reads as 3:46 AM on an unconverted UTC clock,
making the market look closed when it's actually open.

now_ist()/today_ist() deliberately return NAIVE datetime/date objects
(the IST wall-clock reading, with tzinfo stripped) rather than
timezone-AWARE ones - this matches the type of every datetime.now() call
being replaced, so it drops in without risk of mixing aware/naive values
with data already on disk (price history, trade logs) that was written
as naive timestamps.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """Current IST civil time, as a naive datetime - correct regardless
    of the host's own system timezone."""
    return datetime.now(IST).replace(tzinfo=None)


def today_ist() -> date:
    """Current IST calendar date."""
    return now_ist().date()


def is_trading_day(day: date | None = None) -> bool:
    """True Monday-Friday, False on Saturday/Sunday. Every market-open
    check in this app was previously a pure time-of-day comparison with no
    day-of-week awareness at all, so e.g. 11 AM on a Saturday read as
    "market open" just because the clock happened to fall inside
    9:15-15:30 - confirmed directly (2026-09-26, a Saturday, showing
    market_open_now: true). This does NOT know about NSE/BSE exchange
    holidays (Diwali, Republic Day, etc.) - that needs an actual maintained
    holiday calendar, which is a separate, harder problem; this only fixes
    the weekend case."""
    day = day or today_ist()
    return day.weekday() < 5  # Monday=0 ... Friday=4, Saturday=5, Sunday=6
