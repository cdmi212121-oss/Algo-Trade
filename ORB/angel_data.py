"""Angel One SmartAPI market-data helpers (login, instrument lookup, candles, LTP).

Only data endpoints are used - placeOrder is never called.
"""
import json
import logging
import time as _time
from datetime import date, datetime

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


def load_scrip_master():
    """Download the instrument master once per day and cache it."""
    cache = config.BASE_DIR / f"scrip_master_{date.today():%Y%m%d}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    for old in config.BASE_DIR.glob("scrip_master_*.json"):
        old.unlink()
    log.info("Downloading Angel instrument master...")
    data = requests.get(SCRIP_MASTER_URL, timeout=60).json()
    cache.write_text(json.dumps(data))
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
