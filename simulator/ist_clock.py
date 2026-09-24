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
