"""
Paper trading platform: one Flask app, one shared TradingEngine.

Pages: Dashboard, Trade Console (manual paper orders incl. GTT), Active
Position, Trade History, Violation Log. Profile (risk settings) is reachable
from the top bar but isn't a main nav tab.

    python app.py
    -> open http://127.0.0.1:5000 locally, or the platform's assigned URL
       when deployed (PORT env var is honored; binds to 0.0.0.0)

No real orders are ever placed anywhere in this app - AngelOneClient
(angel_data.py) is used for market data only; there is no order-placement
code path anywhere in this codebase.
"""

from __future__ import annotations

import csv
import glob
import logging
import os
import sys
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from candles import build_candles
from engine import INDEX_SYMBOLS, TRADABLE_SYMBOLS, TradingEngine

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # never let browsers cache stale JS/CSS during active development

try:
    engine = TradingEngine()
except Exception:
    # Fail loudly and clearly in the platform's runtime logs (e.g. Voroa)
    # rather than an unadorned traceback - this only happens for a missing/
    # invalid env var (see angel_data.py's _require_env), never a partial
    # or slow Angel One login (that happens lazily, in the background
    # thread, well after this point).
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("app").critical(
        "Failed to initialize the trading engine at startup - check that "
        "ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, and ANGEL_TOTP_SECRET "
        "are all set correctly.", exc_info=True,
    )
    sys.exit(1)

TRADE_LOG_DIR = "logs"
PRICE_HISTORY_DIR = "price_history"


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
        payload["error"] = engine.last_error
    return jsonify(payload)


@app.route("/api/candles/<symbol>")
def api_candles(symbol: str):
    symbol = symbol.upper()
    day = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
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
    """Liveness check for Voroa (or any host) - deliberately doesn't expose
    positions/P&L/credentials, just confirms the process and engine thread
    are alive."""
    with engine.lock:
        engine_alive = engine.last_updated is not None
    return jsonify({"status": "ok", "engine_alive": engine_alive})


if __name__ == "__main__":
    engine.start()
    # 0.0.0.0 (not 127.0.0.1) so the container accepts connections from
    # outside itself; PORT comes from the platform when deployed (falls
    # back to 5000 for local runs where nothing sets it).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
