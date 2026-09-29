"""Angel One SmartAPI market-data helpers (login, instrument lookup, candles, LTP).

Only data endpoints are used - placeOrder is never called.
"""
import json
import logging
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

import pyotp
import requests
from SmartApi import SmartConnect

import config

log = logging.getLogger("angel")
SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


def login():
    if not all([config.API_KEY, config.CLIENT_CODE, config.MPIN, config.TOTP_SECRET]):
        raise SystemExit("Missing credentials - copy .env.example to .env and fill it in.")
    api = SmartConnect(api_key=config.API_KEY)
    totp = pyotp.TOTP(config.TOTP_SECRET).now()
    resp = api.generateSession(config.CLIENT_CODE, config.MPIN, totp)
    if not resp or not resp.get("status"):
        raise SystemExit(f"Angel login failed: {resp}")
    log.info("Logged in to Angel One as %s", config.CLIENT_CODE)
    return api


MAIN_SIMULATOR_CACHE = Path(__file__).resolve().parent.parent / "simulator" / "tools" / "cache" / "angel_instruments.json"


def _atomic_write(path: Path, data) -> None:
    """Write via a temp file + os.replace (a single OS-level rename), so a
    process killed mid-write can never leave a truncated/corrupted cache
    file behind - either the old complete file stays, or the new complete
    file replaces it, never a mix."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def load_scrip_master():
    """Download the instrument master once per day and cache it."""
    cache = config.BASE_DIR / f"scrip_master_{date.today():%Y%m%d}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except json.JSONDecodeError:
            log.warning("Today's instrument cache is corrupted - refetching.")

    # Reuse the main simulator's own cache if it's fresh (same file, same
    # data - it's the identical public instrument list, not account-
    # specific) - avoids an unnecessary ~36MB download right in this exact
    # critical 9:30-9:45 AM window, when Angel's own rate limiter has
    # already been observed to be touchy (2026-09-28).
    try:
        if MAIN_SIMULATOR_CACHE.exists():
            age = datetime.now() - datetime.fromtimestamp(MAIN_SIMULATOR_CACHE.stat().st_mtime)
            if age < timedelta(hours=20):
                data = json.loads(MAIN_SIMULATOR_CACHE.read_text())
                _atomic_write(cache, data)
                log.info("Reused main simulator's instrument cache (%.1fh old) - skipped the download.",
                         age.total_seconds() / 3600)
                return data
    except Exception:
        log.exception("Could not reuse main simulator's instrument cache (non-fatal, falling back to download)")

    for old in config.BASE_DIR.glob("scrip_master_*.json"):
        old.unlink()
    log.info("Downloading Angel instrument master...")
    data = requests.get(SCRIP_MASTER_URL, timeout=60).json()
    _atomic_write(cache, data)
    return data


def find_atm_options(master, atm_strike, today=None):
    """Return (ce, pe) instrument dicts for NIFTY at the nearest expiry."""
    today = today or date.today()
    opts = []
    for r in master:
        if r.get("name") != "NIFTY" or r.get("instrumenttype") != "OPTIDX" or r.get("exch_seg") != "NFO":
            continue
        try:
            exp = datetime.strptime(r["expiry"], "%d%b%Y").date()
            strike = float(r["strike"]) / 100          # Angel stores strike x 100
        except (KeyError, ValueError):
            continue
        if exp >= today and strike == atm_strike:
            opts.append((exp, r))
    if not opts:
        raise RuntimeError(f"No NIFTY options found for strike {atm_strike}")
    nearest = min(e for e, _ in opts)
    ce = next(r for e, r in opts if e == nearest and r["symbol"].endswith("CE"))
    pe = next(r for e, r in opts if e == nearest and r["symbol"].endswith("PE"))
    return ce, pe


def range_candles(api, exchange, token, day):
    """1-minute candles inside RANGE_START..RANGE_END for `day`."""
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": f"{day:%Y-%m-%d} {config.RANGE_START:%H:%M}",
        "todate": f"{day:%Y-%m-%d} {config.RANGE_END:%H:%M}",
    }
    resp = api.getCandleData(params)
    _time.sleep(0.4)                                   # candle API limit ~3 req/sec
    if not resp or not resp.get("status") or not resp.get("data"):
        raise RuntimeError(f"Candle fetch failed for {token}: {resp}")
    start, end = config.RANGE_START.strftime("%H:%M"), config.RANGE_END.strftime("%H:%M")
    # timestamp format: 2026-09-24T09:42:00+05:30
    return [c for c in resp["data"] if start <= c[0][11:16] < end]


def high_low(candles):
    return max(c[2] for c in candles), min(c[3] for c in candles)


def get_ltps(api, spot_token, ce_token, pe_token):
    """One call for all three LTPs. Returns (spot, ce, pe)."""
    resp = api.getMarketData("LTP", {"NSE": [spot_token], "NFO": [ce_token, pe_token]})
    if not resp or not resp.get("status"):
        raise RuntimeError(f"LTP fetch failed: {resp}")
    ltp = {d["symbolToken"]: float(d["ltp"]) for d in resp["data"]["fetched"]}
    return ltp[spot_token], ltp[ce_token], ltp[pe_token]
