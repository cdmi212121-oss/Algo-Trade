"""
Paper trading platform: one Flask app, one shared TradingEngine.

Pages: Dashboard, Trade Console (manual paper orders incl. GTT), Active
Position, Trade History, Violation Log. Profile (risk settings) is reachable
from the top bar but isn't a main nav tab.

Local dev:
    python app.py
    -> http://127.0.0.1:5000 (or PORT if set)

Production (Voroa or any WSGI host):
    gunicorn --chdir simulator app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120

No real orders are ever placed anywhere in this app - AngelOneClient
(angel_data.py) is used for market data only; there is no order-placement
code path anywhere in this codebase.

Startup architecture (important for Gunicorn/Voroa):
The Flask `app` object and every route below are ready the instant this
module finishes importing, regardless of Angel One. The TradingEngine
(which needs ANGEL_API_KEY/ANGEL_CLIENT_CODE/ANGEL_PIN/ANGEL_TOTP_SECRET and
talks to Angel One) is constructed and started in a background daemon
thread kicked off at the bottom of this module - never inline in the
import path. If Angel One is unreachable, credentials are wrong, or the
engine throws for any other reason, that thread logs the failure and dies;
Flask, every other route, and /health all keep working regardless. This
matters specifically because Gunicorn imports this module as `app:app` -
it never runs the `if __name__ == "__main__":` block, so nothing engine-
related can be gated behind that block, and nothing that can fail must
run directly in module-level code outside a try/except.
"""

from __future__ import annotations

import concurrent.futures
import csv
import glob
import logging
import os
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from candles import build_candles
from engine import INDEX_SYMBOLS, TRADABLE_SYMBOLS, TradingEngine
from ist_clock import now_ist

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

print("Starting Algo-Trade Flask application...")

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # never let browsers cache stale JS/CSS during active development

TRADE_LOG_DIR = "logs"
PRICE_HISTORY_DIR = "price_history"

# engine starts as None and stays None if the background startup thread
# never succeeds (e.g. bad/missing Angel One credentials) - every route
# below except /health reads through this, and will 500 on a real request
# if the engine never came up. /health never touches it.
engine: TradingEngine | None = None
engine_error: str | None = None  # str(exception) if startup failed - surfaced on /health for easy remote diagnosis
engine_checkpoint: str = "not_started"  # last reached step - pinpoints a HANG (no exception, no success) on /health
_engine_start_lock = threading.Lock()
_engine_started = False


def _set_checkpoint(name: str) -> None:
    global engine_checkpoint
    engine_checkpoint = name
    log.info("Engine startup checkpoint: %s", name)


def _start_engine_once() -> None:
    """Build and start the TradingEngine exactly once, in a background
    daemon thread. Safe to call more than once (e.g. if a request handler
    ever wanted to trigger it) - only the first call does anything, and the
    lock keeps that decision atomic. Runs fully off the import path, so a
    slow or failing Angel One connection can never delay or crash Flask's
    own startup."""
    global _engine_started
    with _engine_start_lock:
        if _engine_started:
            return
        _engine_started = True

    def _run() -> None:
        global engine, engine_error
        _set_checkpoint("thread_started")
        log.info("Trading engine startup initiated.")
        try:
            _set_checkpoint("constructing_trading_engine")
            # TradingEngine() constructs AngelOneClient() internally, which
            # constructs SmartApi's SmartConnect() - that third-party class
            # does its OWN unguarded filesystem write during __init__
            # (creates logs/<date>/app.log via logzero.logfile()), completely
            # outside our control since it's installed, not our code. On some
            # hosts' filesystems that can hang indefinitely instead of
            # failing fast. Run it with a hard timeout so a hang there can
            # never leave the engine stuck forever with no explanation.
            #
            # Deliberately NOT using "with ThreadPoolExecutor(...) as pool:"
            # here - exiting that block calls pool.shutdown(wait=True), which
            # blocks until the stuck submitted call finishes, silently
            # defeating the whole timeout. Leaving the pool (and its one
            # stuck worker thread, if it never returns) to be garbage
            # collected is the correct trade-off here - it's a one-time
            # startup attempt, not a per-request cost.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = pool.submit(TradingEngine)
            try:
                new_engine = future.result(timeout=25)
            except concurrent.futures.TimeoutError:
                raise RuntimeError(
                    "TradingEngine() construction did not finish within 25s "
                    "- most likely SmartApi's own SmartConnect() hanging on "
                    "a filesystem write (logs/<date>/app.log) during "
                    "__init__, on this host's filesystem. Not our own code "
                    "hanging - see angel_data.py/SmartApi's smartConnect.py."
                )
            _set_checkpoint("calling_start")
            new_engine.start()
            _set_checkpoint("done")
            engine = new_engine
            log.info("Trading engine started successfully.")
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            log.critical(
                "Trading engine failed to start (commonly: missing/invalid "
                "ANGEL_API_KEY/ANGEL_CLIENT_CODE/ANGEL_PIN/ANGEL_TOTP_SECRET, "
                "or Angel One being unreachable, or a hang inside SmartApi's "
                "own filesystem write during SmartConnect() init). Flask "
                "keeps running regardless - only the dashboard/API routes "
                "are affected, not /health.",
                exc_info=True,
            )

    threading.Thread(target=_run, daemon=True, name="engine-startup").start()


_start_engine_once()

print("Web server is ready.")


# ---- pages -----------------------------------------------------------------

@app.route("/")
def page_dashboard():
    return render_template("dashboard.html", index_symbols=INDEX_SYMBOLS, active="dashboard")


@app.route("/trade")
def page_trade():
    return render_template("trade.html", tradable_symbols=TRADABLE_SYMBOLS, active="trade")


@app.route("/positions")
def page_positions():
    return render_template("positions.html", active="positions")


@app.route("/history")
def page_history():
    return render_template("history.html", active="history")


@app.route("/violations")
def page_violations():
    return render_template("violations.html", active="violations")


@app.route("/profile")
def page_profile():
    return render_template("profile.html", active="profile")


# ---- top-bar status, shared across every page -------------------------

@app.route("/api/topbar")
def api_topbar():
    budget = engine.get_budget_status()
    return jsonify(budget)


@app.route("/api/order_events")
def api_order_events():
    since_id = int(request.args.get("since", 0))
    return jsonify(engine.get_order_events(since_id))


# ---- shared read APIs --------------------------------------------------

@app.route("/api/spot")
def api_spot():
    with engine.lock:
        payload = {
            symbol: {**engine.spot_snapshot.get(symbol, {}), "sparkline": list(engine.sparklines[symbol])}
            for symbol in INDEX_SYMBOLS
        }
        payload["updated_at"] = engine.last_updated
        # engine.last_error deliberately not exposed here - transient network
        # errors (timeouts etc.) are retried automatically every poll and
        # aren't actionable from the dashboard; they're still in
        # logs/runtime.log for debugging.
    return jsonify(payload)


@app.route("/api/candles/<symbol>")
def api_candles(symbol: str):
    symbol = symbol.upper()
    day = request.args.get("date") or now_ist().strftime("%Y-%m-%d")
    interval = int(request.args.get("interval", 1))
    return jsonify(build_candles(symbol, day, interval))


@app.route("/api/positions")
def api_positions():
    return jsonify(engine.get_positions())


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    data = request.get_json(force=True)
    result = engine.close_manual(data["symbol"])
    return jsonify(result)


@app.route("/api/cancel_order", methods=["POST"])
def api_cancel_order():
    data = request.get_json(force=True)
    result = engine.cancel_pending_order(data["symbol"])
    return jsonify(result)


@app.route("/api/option_chain/<symbol>")
def api_option_chain(symbol: str):
    symbol = symbol.upper()
    with engine.lock:
        snapshot = engine.option_snapshots.get(symbol)
    if snapshot is None:
        return jsonify({"symbol": symbol, "spot": None, "expiry": None, "quotes": []})
    return jsonify({
        "symbol": symbol,
        "spot": snapshot.underlying_value,
        "expiry": snapshot.nearest_expiry(),
        "lot_size": snapshot.lot_size,
        "quotes": [
            {"strike": q.strike, "option_type": q.option_type, "ltp": q.ltp,
             "oi": q.oi, "volume": q.volume, "bid": q.bid_price, "ask": q.ask_price,
             "day_high": q.day_high, "day_low": q.day_low,
             "net_change": q.net_change, "pct_change": q.pct_change}
            for q in snapshot.quotes
        ],
    })


@app.route("/api/trade", methods=["POST"])
def api_trade():
    data = request.get_json(force=True)
    result = engine.place_manual_trade(
        symbol=data["symbol"].upper(),
        strike=float(data["strike"]),
        expiry=data["expiry"],
        option_type=data["option_type"].upper(),
        side=data["side"],
        qty=int(data["qty"]),
        stop_loss=float(data["stop_loss"]),
        target=float(data["target"]),
        order_type=data.get("order_type", "MARKET"),
        trigger_price=float(data["trigger_price"]) if data.get("trigger_price") is not None else None,
    )
    return jsonify(result)


@app.route("/api/trades")
def api_trades():
    trades = []
    for path in sorted(glob.glob(os.path.join(TRADE_LOG_DIR, "trades_*.csv"))):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                trades.append(row)
    trades.sort(key=lambda r: r.get("exit_time", ""), reverse=True)

    total_pnl = sum(float(t["pnl"]) for t in trades) if trades else 0.0
    wins = [t for t in trades if float(t["pnl"]) > 0]
    summary = {
        "total_trades": len(trades),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "total_pnl": round(total_pnl, 2),
        "current_equity": round(engine.config.starting_capital + total_pnl, 2),
        "starting_capital": engine.config.starting_capital,
    }
    return jsonify({"trades": trades, "summary": summary})


@app.route("/api/pnl_curve")
def api_pnl_curve():
    trades = []
    for path in sorted(glob.glob(os.path.join(TRADE_LOG_DIR, "trades_*.csv"))):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                trades.append(row)
    trades.sort(key=lambda r: r.get("exit_time", ""))

    cumulative = engine.config.starting_capital
    curve = []
    for t in trades:
        cumulative += float(t["pnl"])
        curve.append({"t": t["exit_time"], "equity": round(cumulative, 2)})
    return jsonify(curve)


@app.route("/api/violations")
def api_violations():
    return jsonify(engine.get_violations())


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "POST":
        data = request.get_json(force=True)
        cfg = engine.config
        with engine.lock:
            if "starting_capital" in data:
                engine.update_capital(float(data["starting_capital"]))
            if "tradable_capital_pct" in data:
                cfg.tradable_capital_pct = float(data["tradable_capital_pct"])
            if "risk_per_trade_pct" in data:
                cfg.risk_per_trade_pct = float(data["risk_per_trade_pct"])
            if "max_daily_loss_pct" in data:
                cfg.max_daily_loss_pct = float(data["max_daily_loss_pct"])
            if "max_trades_per_day" in data:
                cfg.max_trades_per_day = int(data["max_trades_per_day"])
            if "max_concurrent_positions" in data:
                cfg.max_concurrent_positions = int(data["max_concurrent_positions"])
            if "symbols" in data:
                valid = [s for s in data["symbols"] if s in TRADABLE_SYMBOLS]
                cfg.symbols = valid or cfg.symbols
        return jsonify({"ok": True})

    masked_client_code = engine.client.client_code[:3] + "***" + engine.client.client_code[-2:]
    return jsonify({
        "client_code": masked_client_code,
        "starting_capital": engine.config.starting_capital,
        "tradable_capital_pct": engine.config.tradable_capital_pct,
        "risk_per_trade_pct": engine.config.risk_per_trade_pct,
        "max_daily_loss_pct": engine.config.max_daily_loss_pct,
        "max_trades_per_day": engine.config.max_trades_per_day,
        "max_concurrent_positions": engine.config.max_concurrent_positions,
        "symbols": engine.config.symbols,
        "tradable_symbols": TRADABLE_SYMBOLS,
        "market_open": engine.config.market_open.strftime("%H:%M"),
        "market_close": engine.config.market_close.strftime("%H:%M"),
    })


@app.route("/health")
def api_health():
    """Liveness check for Voroa (or any host). Deliberately independent of
    TradingEngine/AngelOneClient/Angel One login/TOTP/market data/strategy
    execution - must return 200 even if all of those are broken, unstarted,
    or mid-crash, so a data-feed problem never takes down the whole
    deployment. engine_running/engine_error are informational only (plain
    variable reads, no lock, can't throw) - they never affect the status
    code. engine_error is just str(exception) - never a full traceback,
    never anything from the request itself - so it's safe to expose here."""
    return jsonify({
        "status": "ok",
        "engine_running": engine is not None,
        "engine_error": engine_error,
        "engine_checkpoint": engine_checkpoint,
    })


if __name__ == "__main__":
    # 0.0.0.0 (not 127.0.0.1) so the container accepts connections from
    # outside itself; PORT comes from the platform when deployed (falls
    # back to 5000 for local runs where nothing sets it). Gunicorn in
    # production never executes this block at all - it binds via its own
    # --bind 0.0.0.0:$PORT flag instead; the engine startup thread above
    # already runs unconditionally at import time either way.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
