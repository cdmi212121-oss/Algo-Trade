"""Settings for the Nifty Opening Range (09:42-09:45) paper-trading algo."""
import os
from datetime import time, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
BASE_DIR = Path(__file__).resolve().parent


def _load_env(path=BASE_DIR / ".env"):
    """Minimal .env reader (KEY=VALUE per line) so no extra package is needed."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# ===== Angel One SmartAPI credentials (put them in .env, never in code) =====
API_KEY = os.environ.get("ANGEL_API_KEY", "")
CLIENT_CODE = os.environ.get("ANGEL_CLIENT_CODE", "")
MPIN = os.environ.get("ANGEL_MPIN", "")
TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")

# ===== Opening range window (same as the TradingView indicator) =====
RANGE_START = time(9, 42)   # inclusive
RANGE_END = time(9, 45)     # exclusive -> candles 09:42, 09:43, 09:44

# ===== Instrument =====
SPOT_EXCHANGE = "NSE"
SPOT_TOKEN = "99926000"     # NIFTY 50 index token on Angel One
SPOT_SYMBOL = "Nifty 50"
STRIKE_STEP = 50
LOTS = 1                    # lot size is read from Angel's instrument master

# ===== Strategy rules =====
BREAKOUT_BUFFER = 0.0       # extra points above/below the line before it counts as a break
RISK_REWARD = 2.0           # 1:2 target
# "fixed":   SL = entry premium - SL_POINTS,   target = entry + 2 x SL_POINTS (on premium)
# "premium": SL = option's own 09:42-09:45 low, target = entry + 2 x risk (on premium)
# "spot":    SL = spot hits opposite line,     target = 2 x risk on spot
SL_MODE = "fixed"
SL_POINTS = 20.0
USE_TARGET = False          # False = no fixed 1:2 exit, let the trailing SL ride the move

# ===== Trailing SL (in R = initial risk, i.e. 20 points with SL_MODE="fixed") =====
# +1R -> SL to cost, +2R -> SL at +1R, +3R -> SL at +2R, ...
TRAIL_ENABLED = True
TRAIL_START_R = 1.0         # start trailing once profit reaches this many R
TRAIL_LOCK_AT_START_R = 0.0 # SL locks this many R at the start (0 = breakeven)
TRAIL_STEP_R = 1.0          # every further R of profit moves SL up by this much
MAX_TRADES_PER_DAY = 1
NO_NEW_ENTRY_AFTER = time(14, 30)
SQUARE_OFF_TIME = time(15, 15)
POLL_SECONDS = 1.0

# ===== Paper trading =====
PAPER_TRADING = True        # this code never sends real orders; kept as a safety flag
TRADE_LOG_DIR = BASE_DIR / "trade_logs"
