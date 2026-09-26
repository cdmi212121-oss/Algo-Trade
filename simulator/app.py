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

Startup architecture (important for Gunicorn/Render):
The Flask `app` object and every route below are ready the instant this
module finishes importing. TradingEngine construction is now pure-local
(TradingConfig/PaperBroker/RiskManager/StrategyEngine - no network, no
subprocess) and provably fast, so it happens directly below, synchronously,
wrapped in a try/except - the only way it can fail is a missing/invalid
required env var, which is immediate and clear, never a hang.

The Angel One connection (the one part that talks to a third-party service
and has been observed to occasionally be slow or fail) lives entirely
inside TradingEngine now (engine.client starts None, a background thread
retries indefinitely - see engine.py's TradingEngine docstring) and can
NEVER block engine construction, Flask startup, or any route that doesn't
itself need live prices. This is the actual fix for Profile "not showing":
Profile never needed Angel One in the first place, it was only ever gated
behind it by an incidental architecture decision - now it isn't.
"""

from __future__ import annotations

import csv
import glob
import logging
import multiprocessing
import os
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

# engine stays None only if construction itself raised (missing/invalid
# required env var - immediate and rare, not a hang). Every /api/* route
# below reads through this and gets a clean 503 rather than a crash if so.
engine: TradingEngine | None = None
engine_error: str | None = None


def _engine_ready():
    """Returns a clean JSON 503 if engine construction itself failed
    (missing/invalid env var), or None when ready - which in practice is
    almost immediately after this module imports, every time, since
    construction no longer does anything that can be slow. This does NOT
    check whether the Angel One data feed is connected - routes that
    specifically need live prices check engine.client themselves."""
    if engine is None:
        return jsonify({"error": "Trading engine failed to start.", "engine_error": engine_error}), 503
    return None


def _start_engine_once() -> None:
    global engine, engine_error
    try:
        new_engine = TradingEngine()
        new_engine.start()
        engine = new_engine
        log.info("Trading engine started (Angel One data feed connecting in the background).")
    except Exception as exc:
        engine_error = f"{type(exc).__name__}: {exc}"
        log.critical("Trading engine failed to start: %s", engine_error, exc_info=True)


# AngelOneClientProxy (inside TradingEngine, started via engine.start())
# spawns a child process for the real Angel One connection - "fork" on
# Linux/Render, "spawn" on Windows (fork isn't available there at all; see
# angel_data.py). With "spawn", the child reconstructs its __main__ module
# by re-importing whatever module was __main__ in the parent - for local
# `python app.py` runs, that's this module, so without this guard the child
# would also hit this exact line and recursively try to start its own
# nested engine. Under Gunicorn (`gunicorn app:app`) or with "fork",
# __main__ is never this module, so this guard is a no-op there - but it's
# cheap and correct to have unconditionally either way.
if multiprocessing.current_process().name == "MainProcess":
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


# ---- ORB (separate standalone algo in ../ORB) status -------------------
# Deliberately independent of `engine`/_engine_ready() - ORB runs its own
# process with its own Angel One session, so this must work even if the
# main simulator's engine is intentionally not running today (session-
# conflict avoidance - both currently share one API key). This is a pure
# read of ORB's own trade log CSV, nothing live.

ORB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ORB")


@app.route("/api/orb_status")
def api_orb_status():
    path = os.path.join(ORB_DIR, "trade_logs", f"trades_{now_ist():%Y%m%d}.csv")
    if not os.path.exists(path):
        return jsonify({"exists": False, "rows": [], "open_position": None})

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))

    open_position = None
    if rows and rows[-1]["action"] in ("ENTRY", "TRAIL"):
        entry_row = next(r for r in rows if r["action"] == "ENTRY")
        last = rows[-1]
        open_position = {
            "side": entry_row["side"],
            "symbol": entry_row["symbol"],
            "qty": entry_row["qty"],
            "entry_price": entry_row["price"],
            "entry_time": entry_row["time"],
            "current_sl": last["sl"] or entry_row["sl"],
            "target": entry_row["target"],
        }

    return jsonify({"exists": True, "rows": rows, "open_position": open_position})


# ---- top-bar status, shared across every page -------------------------

@app.route("/api/topbar")
def api_topbar():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    budget = engine.get_budget_status()
    return jsonify(budget)


@app.route("/api/order_events")
def api_order_events():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    since_id = int(request.args.get("since", 0))
    return jsonify(engine.get_order_events(since_id))


# ---- shared read APIs --------------------------------------------------

@app.route("/api/spot")
def api_spot():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    return jsonify(engine.get_positions())


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    data = request.get_json(force=True)
    result = engine.close_manual(data["symbol"])
    return jsonify(result)


@app.route("/api/cancel_order", methods=["POST"])
def api_cancel_order():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    data = request.get_json(force=True)
    result = engine.cancel_pending_order(data["symbol"])
    return jsonify(result)


@app.route("/api/option_chain/<symbol>")
def api_option_chain(symbol: str):
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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
    if (not_ready := _engine_ready()) is not None:
        return not_ready
    return jsonify(engine.get_violations())


@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if (not_ready := _engine_ready()) is not None:
        return not_ready
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

    masked_client_code = engine.client_code[:3] + "***" + engine.client_code[-2:]
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
    """Liveness check for Render (or any host). Always 200 - engine
    construction is pure-local and provably fast now, so `engine is None`
    only ever means a genuine, immediate startup failure (missing/invalid
    env var), which is informational here, not a reason to fail the health
    check (Flask itself is still fully up and serving). The Angel One data
    feed's own status (data_feed_checkpoint/data_feed_error) is similarly
    informational only - it retries indefinitely on its own and restarting
    the whole app would not help a slow/unreachable third-party service
    reconnect any faster, so this deliberately never fails the health check
    for that either."""
    payload = {
        "status": "ok",
        "engine_running": engine is not None,
        "engine_error": engine_error,
        "data_feed_checkpoint": engine.data_feed_checkpoint if engine else None,
        "data_feed_error": engine.data_feed_error if engine else None,
    }
    return jsonify(payload)


if __name__ == "__main__":
    # 0.0.0.0 (not 127.0.0.1) so the container accepts connections from
    # outside itself; PORT comes from the platform when deployed (falls
    # back to 5000 for local runs where nothing sets it). Gunicorn in
    # production never executes this block at all - it binds via its own
    # --bind 0.0.0.0:$PORT flag instead; the engine startup thread above
    # already runs unconditionally at import time either way.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
