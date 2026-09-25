"""Nifty Opening Range (09:42-09:45) breakout - PAPER TRADING via Angel One data.

Run:  python main.py
"""
import logging
import time as _time
from datetime import datetime, timedelta

import angel_data as ad
import config
from paper_broker import PaperBroker
from strategy import ORBStrategy, Range

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("main")


def now_ist():
    return datetime.now(config.IST)


def wait_until(t):
    target = now_ist().replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
    while now_ist() < target:
        _time.sleep(min(30, (target - now_ist()).total_seconds() + 0.1))


def build_ranges(api, day):
    spot_c = ad.range_candles(api, config.SPOT_EXCHANGE, config.SPOT_TOKEN, day)
    if len(spot_c) < 3:
        raise RuntimeError(f"Expected 3 spot candles, got {len(spot_c)}")
    spot_hi, spot_lo = ad.high_low(spot_c)
    atm = round(spot_c[-1][4] / config.STRIKE_STEP) * config.STRIKE_STEP

    ce, pe = ad.find_atm_options(ad.load_scrip_master(), atm, day)
    ce_hi, ce_lo = ad.high_low(ad.range_candles(api, "NFO", ce["token"], day))
    pe_hi, pe_lo = ad.high_low(ad.range_candles(api, "NFO", pe["token"], day))

    log.info("SPOT range  H %.2f  L %.2f   ATM %d", spot_hi, spot_lo, atm)
    log.info("%s range  H %.2f  L %.2f", ce["symbol"], ce_hi, ce_lo)
    log.info("%s range  H %.2f  L %.2f", pe["symbol"], pe_hi, pe_lo)
    strat = ORBStrategy(Range(spot_hi, spot_lo), Range(ce_hi, ce_lo), Range(pe_hi, pe_lo))
    return strat, ce, pe


def main():
    assert config.PAPER_TRADING, "This script is paper-trading only."
    api = ad.login()

    # candles for 09:44 are complete after 09:45; small delay for the API to publish them
    ready = (datetime.combine(now_ist().date(), config.RANGE_END) + timedelta(seconds=10)).time()
    log.info("Waiting for opening range to complete (%s)...", ready)
    wait_until(ready)

    day = now_ist().date()
    for attempt in range(5):
        try:
            strat, ce, pe = build_ranges(api, day)
            break
        except Exception as e:
            log.warning("Range build failed (%s), retrying...", e)
            _time.sleep(5)
    else:
        raise SystemExit("Could not build opening range.")

    broker = PaperBroker()
    instruments = {"CE": ce, "PE": pe}
    log.info("Watching for breakout (paper trades -> %s)", broker.path)

    try:
        while True:
            now = now_ist()
            try:
                spot, ce_ltp, pe_ltp = ad.get_ltps(api, config.SPOT_TOKEN, ce["token"], pe["token"])
            except Exception as e:
                log.warning("LTP error: %s", e)
                _time.sleep(config.POLL_SECONDS)
                continue

            for act in strat.on_tick(now, spot, ce_ltp, pe_ltp):
                inst = instruments[act["side"]]
                qty = int(inst["lotsize"]) * config.LOTS
                broker.execute(act, inst["symbol"], inst["token"], qty)

            done = strat.position is None and (
                strat.trades_taken >= strat.max_trades or now.time() >= config.NO_NEW_ENTRY_AFTER)
            if done:
                log.info("Done for the day. Trades: %d", strat.trades_taken)
                break
            _time.sleep(config.POLL_SECONDS)
    finally:
        broker.close()
        try:
            api.terminateSession(config.CLIENT_CODE)
        except Exception:
            pass


if __name__ == "__main__":
    main()
