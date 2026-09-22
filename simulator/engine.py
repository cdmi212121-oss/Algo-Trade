"""
Shared trading engine: one background loop that polls live data, drives the
(pending) strategy engine for automatic entries, monitors SL/target/trailing
exits, and also accepts manual paper trades placed through the dashboard's
Trade page. Both automatic and manual positions live in the same PaperBroker,
so Positions/History/P&L reflect either kind uniformly.

Runs inside the Flask process (app.py) as a daemon thread - there is exactly
one engine per running app, holding the one source of truth for prices,
positions, and P&L that every dashboard page reads from.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime
from datetime import time as dtime

try:
    import winsound  # Windows-only stdlib module - audible alert on auto-entry
except ImportError:
    winsound = None  # e.g. Voroa's Linux container - alert becomes a no-op there

from angel_data import AngelOneClient
from candles import candles_from_ticks
from config import TradingConfig
from market_data import OptionChainSnapshot, instrument_key
from paper_broker import ExitReason, OrderType, PaperBroker, Side
from risk_manager import RiskManager
from strategy_engine import StrategyEngine, candle_interval

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("logs/runtime.log")],
)
log = logging.getLogger("engine")

INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "SENSEX"]
TRADABLE_SYMBOLS = ["NIFTY", "BANKNIFTY", "SENSEX"]  # option chains available for all three (see angel_data)
SPARKLINE_POINTS = 60
PRICE_HISTORY_DIR = "price_history"

# Per explicit user instruction: never call Angel One's API at all outside
# this window - no spot poll, no option chain fetch, nothing. Slightly
# wider than config.market_open/market_close (9:15/15:30, used for the
# strategy's own trading-hours gate) since the user asked specifically for
# 9:10 as the data-fetch start.
DATA_POLL_START = dtime(9, 10)
DATA_POLL_END = dtime(15, 30)


class TradingEngine:
    def __init__(self):
        self.config = TradingConfig()
        self.client = AngelOneClient()
        self.broker = PaperBroker(starting_capital=self.config.starting_capital)
        self.risk = RiskManager(self.config)
        self.strategy = StrategyEngine()

        self.lock = threading.RLock()
        self.ltp_map: dict[str, float] = {}
        self.spot_snapshot: dict[str, dict] = {}
        self.sparklines: dict[str, deque] = {s: deque(maxlen=SPARKLINE_POINTS) for s in INDEX_SYMBOLS}
        self.option_snapshots: dict[str, OptionChainSnapshot] = {}
        self.last_updated: str | None = None
        self.last_error: str | None = None
        self.market_open_now: bool = False
        self._squared_off_today = False
        self._position_ticks: dict[str, deque] = {}  # per open position, for scale-out candle detection
        self.order_events: deque = deque(maxlen=100)  # for the UI's order-placed/filled/rejected/cancelled toasts
        self._next_event_id = 1

    def _alert_position_created(self) -> None:
        """Audible alert the moment the algo itself opens a position (auto
        entry or a triggered GTT) - so it doesn't take watching the
        dashboard or asking to notice. Never let a sound failure (e.g. no
        audio device, or running on a non-Windows host) break the engine
        loop - it's a nice-to-have, not core logic."""
        if winsound is None:
            return
        try:
            for _ in range(3):
                winsound.Beep(1500, 300)
        except Exception:
            pass

    def _log_event(self, trade_id: str, event_type: str, message: str) -> None:
        with self.lock:
            self.order_events.append({
                "id": self._next_event_id,
                "time": datetime.now().isoformat(timespec="seconds"),
                "trade_id": trade_id,
                "type": event_type,  # PLACED, FILLED, REJECTED, CANCELLED, EXITED
                "message": message,
            })
            self._next_event_id += 1

    # ---- background loop -------------------------------------------------

    def start(self) -> None:
        thread = threading.Thread(target=self._run_forever, daemon=True)
        thread.start()

    def _run_forever(self) -> None:
        log.info("Trading engine starting. Symbols=%s poll=%ss. No real orders are ever placed.",
                  self.config.symbols, self.config.poll_interval_seconds)
        while True:
            try:
                self._tick()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                log.exception("Engine tick failed")
            time.sleep(self.config.poll_interval_seconds)

    def _tick(self) -> None:
        now_dt = datetime.now()
        now = now_dt.time()

        with self.lock:
            have_any_snapshot = bool(self.spot_snapshot) and bool(self.option_snapshots)
        if not (DATA_POLL_START <= now <= DATA_POLL_END) and have_any_snapshot:
            # Outside 9:10-15:30 and we already have at least one snapshot -
            # don't call Angel One's API again (no spot poll, no option
            # chain fetch). Dashboard keeps showing whatever was last
            # fetched; last_updated still advances so /health and the
            # dashboard don't read this as the engine being dead, just quiet
            # by design.
            with self.lock:
                self.last_updated = now_dt.isoformat(timespec="seconds")
                self.market_open_now = False
            return
        # Otherwise (inside the window, OR outside it but we've never
        # fetched anything yet this process) - fall through and fetch once,
        # so a restart outside market hours still shows real previous-
        # session data instead of staying blank all night.

        # Index spot prices - always polled (within the window above),
        # drives dashboard tiles/candles regardless of whether the
        # algo/options side is active.
        spot = self.client.get_spot_snapshot(INDEX_SYMBOLS)
        with self.lock:
            for symbol, data in spot.items():
                self.spot_snapshot[symbol] = data
                self.sparklines[symbol].append(data["ltp"])
            self._append_price_history(now_dt, spot)

        if self.risk.should_square_off(now) and not self._squared_off_today:
            with self.lock:
                closed = self.broker.square_off_all(self.ltp_map)
            for trade in closed:
                log.info("[EOD SQUAREOFF] %s pnl=%.2f", trade.symbol, trade.pnl)
                self._forget_position(trade.symbol)
            self._squared_off_today = True

        market_open_now = self.config.market_open <= now <= self.config.market_close

        # While the market's closed: keep whatever option-chain snapshot we
        # already have (Angel still answers with the last session's data
        # even after hours, so on first boot we fetch it once to show
        # "previous data" immediately) rather than hammering the API for
        # data that isn't going to change. Once the market's actually open,
        # refetch every tick as normal for live updates.
        new_ltp_map: dict[str, float] = {}
        for symbol in self.config.symbols:
            if symbol not in TRADABLE_SYMBOLS:
                continue
            with self.lock:
                already_have_snapshot = symbol in self.option_snapshots
            if not market_open_now and already_have_snapshot:
                continue
            try:
                snapshot = self.client.get_option_chain(symbol, strikes_around_atm=20)
            except Exception:
                log.exception("Failed to fetch option chain for %s", symbol)
                continue

            with self.lock:
                self.option_snapshots[symbol] = snapshot
            for q in snapshot.quotes:
                new_ltp_map[f"{symbol}_{instrument_key(symbol, q)}"] = q.ltp

        with self.lock:
            self.ltp_map.update(new_ltp_map)
            closed = self.broker.check_exits(self.ltp_map)
        for trade in closed:
            log.info("[EXIT] %s %s pnl=%.2f", trade.symbol, trade.exit_reason.value, trade.pnl)
            self._forget_position(trade.symbol)
            self._log_event(trade.trade_id, "EXITED",
                              f"{trade.exit_reason.value} exit @ {trade.exit_price:.2f}, pnl {trade.pnl:.2f}")

        self._check_pending_orders()
        self._manage_open_positions(now_dt)

        if market_open_now and not self._squared_off_today:
            self._maybe_auto_enter()

        with self.lock:
            self.last_updated = now_dt.isoformat(timespec="seconds")
            self.market_open_now = market_open_now

    def _forget_position(self, key: str) -> None:
        self.strategy.clear_position_state(key)
        self._position_ticks.pop(key, None)

    def _check_pending_orders(self) -> None:
        """GTT orders: fill when triggered, but re-validate risk at fill
        time too - account state (equity, trade count) may have changed
        while the order sat pending, so a trigger hitting isn't a guarantee
        the trade is still allowed."""
        with self.lock:
            triggerable = [
                (key, order) for key, order in self.broker.pending_orders.items()
                if key in self.ltp_map and order.should_fill(self.ltp_map[key])
            ]

        for key, order in triggerable:
            with self.lock:
                if key not in self.broker.pending_orders:
                    continue  # already handled (e.g. cancelled) since the snapshot above
                current_equity = self.config.starting_capital + self.broker.mark_to_market(self.ltp_map)
                allowed, why = self.risk.can_open_new_trade(current_equity, len(self.broker.positions), symbol=key)
                if not allowed:
                    self.broker.cancel_pending_order(key)
                    self._log_event(order.trade_id, "REJECTED", f"GTT trigger hit but blocked: {why}")
                    log.info("[GTT REJECTED] %s: %s", key, why)
                    continue

                filled = self.broker.check_pending_orders({key: self.ltp_map[key]})
                if filled:
                    self.risk.record_trade_opened()
                    pos = filled[0]
                    self._log_event(pos.trade_id, "FILLED",
                                      f"GTT triggered: {pos.side.value} {pos.qty} @ {pos.entry_price:.2f}")
                    log.info("[GTT FILLED] %s %s qty=%d @ %.2f", key, pos.side.value, pos.qty, pos.entry_price)
                    self._alert_position_created()

    def _manage_open_positions(self, now_dt: datetime) -> None:
        """Momentum-based scale-out (§8) for positions already open, whether
        opened by the algo or manually from the Trade page."""
        interval = candle_interval(now_dt.time())
        with self.lock:
            open_keys = list(self.broker.positions.keys())

        for key in open_keys:
            with self.lock:
                position = self.broker.positions.get(key)
                ltp = self.ltp_map.get(key)
            if position is None or ltp is None:
                continue

            ticks = self._position_ticks.setdefault(key, deque(maxlen=2000))
            ticks.append((now_dt, ltp))
            candles = candles_from_ticks(list(ticks), interval)

            action = self.strategy.manage_position(key, position, candles, ltp)
            if action is None:
                continue

            if action["action"] == "partial_exit":
                with self.lock:
                    if key not in self.broker.positions:
                        continue
                    qty_to_close = round(self.broker.positions[key].qty * action["fraction"])
                    if qty_to_close < 1:
                        continue
                    trade = self.broker.partial_close(key, qty_to_close, ltp, ExitReason.PARTIAL_SCALE_OUT)
                    if key in self.broker.positions:
                        self.broker.move_sl_to_breakeven(key)
                log.info("[SCALE OUT] %s qty=%d @ %.2f pnl=%.2f (remainder SL moved to breakeven)",
                          key, trade.qty, ltp, trade.pnl)

    def _maybe_auto_enter(self) -> None:
        for symbol in self.config.symbols:
            if symbol not in TRADABLE_SYMBOLS:
                continue
            with self.lock:
                snapshot = self.option_snapshots.get(symbol)
                current_equity = self.config.starting_capital + self.broker.mark_to_market(self.ltp_map)
                open_count = len(self.broker.positions)
            if snapshot is None:
                continue

            allowed, why = self.risk.can_open_new_trade(current_equity, open_count)
            if not allowed:
                continue

            signals = self.strategy.on_tick(snapshot)

            for sig in signals:
                key = f"{symbol}_{sig.symbol}"
                with self.lock:
                    if key in self.broker.positions:
                        continue
                    qty = self.risk.position_size(sig.entry_price, sig.stop_loss, snapshot.lot_size, sig.size_multiplier)
                    self.broker.open_position(
                        symbol=key, side=sig.side, qty=qty,
                        entry_price=sig.entry_price, stop_loss=sig.stop_loss,
                        target=sig.target, trail_sl_step=sig.trail_sl_step,
                    )
                    self.risk.record_trade_opened()
                log.info("[AUTO ENTRY] %s %s qty=%d @ %.2f SL=%.2f TGT=%.2f (%s)",
                          key, sig.side.value, qty, sig.entry_price, sig.stop_loss, sig.target, sig.reason)
                self._alert_position_created()

    def _append_price_history(self, ts: datetime, spot: dict[str, dict]) -> None:
        import csv
        day = ts.strftime("%Y-%m-%d")
        os.makedirs(PRICE_HISTORY_DIR, exist_ok=True)
        for symbol, data in spot.items():
            path = os.path.join(PRICE_HISTORY_DIR, f"{symbol}_{day}.csv")
            is_new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                writer = csv.writer(f)
                if is_new:
                    writer.writerow(["timestamp", "ltp"])
                writer.writerow([ts.isoformat(timespec="seconds"), data["ltp"]])

    # ---- manual trading ----------------------------------------------------

    def place_manual_trade(self, symbol: str, strike: float, expiry: str, option_type: str,
                            side: str, qty: int, stop_loss: float, target: float,
                            order_type: str = "MARKET", trigger_price: float | None = None) -> dict:
        with self.lock:
            snapshot = self.option_snapshots.get(symbol)
            if snapshot is None:
                return {"ok": False, "error": f"No live option chain for {symbol} yet - try again shortly."}

            quote = next((q for q in snapshot.quotes
                          if q.strike == strike and q.expiry == expiry and q.option_type == option_type), None)
            if quote is None:
                return {"ok": False, "error": "That strike/expiry/type is not in the current chain."}

            key = f"{symbol}_{instrument_key(symbol, quote)}"
            if key in self.broker.positions or key in self.broker.pending_orders:
                return {"ok": False, "error": "A position or order already exists for that instrument."}

            try:
                side_enum = Side(side.upper())
            except ValueError:
                return {"ok": False, "error": f"Invalid side {side!r}"}

            try:
                order_type_enum = OrderType(order_type.upper())
            except ValueError:
                return {"ok": False, "error": f"Invalid order type {order_type!r}"}

            if order_type_enum == OrderType.GTT:
                if trigger_price is None:
                    return {"ok": False, "error": "GTT orders need a trigger price."}
                order = self.broker.place_pending_order(
                    symbol=key, side=side_enum, qty=qty, trigger_price=trigger_price,
                    stop_loss=stop_loss, target=target,
                )
                self._log_event(order.trade_id, "PLACED",
                                  f"GTT {side_enum.value} {qty} @ trigger {trigger_price:.2f}")
                log.info("[GTT PLACED] %s %s qty=%d trigger=%.2f", key, side_enum.value, qty, trigger_price)
                return {"ok": True, "symbol": key, "trade_id": order.trade_id, "pending": True}

            current_equity = self.config.starting_capital + self.broker.mark_to_market(self.ltp_map)
            allowed, why = self.risk.can_open_new_trade(current_equity, len(self.broker.positions), symbol=key)
            if not allowed:
                self._log_event("-", "REJECTED", f"Market order blocked: {why}")
                return {"ok": False, "error": f"Blocked by risk rules: {why}"}

            pos = self.broker.open_position(
                symbol=key, side=side_enum, qty=qty,
                entry_price=quote.ltp, stop_loss=stop_loss, target=target,
            )
            self.risk.record_trade_opened()
            self._log_event(pos.trade_id, "FILLED", f"{side_enum.value} {qty} @ {quote.ltp:.2f}")
            log.info("[MANUAL ENTRY] %s %s qty=%d @ %.2f SL=%.2f TGT=%.2f",
                      key, side_enum.value, qty, quote.ltp, stop_loss, target)
            return {"ok": True, "symbol": key, "trade_id": pos.trade_id, "entry_price": quote.ltp}

    def cancel_pending_order(self, symbol_key: str) -> dict:
        with self.lock:
            if symbol_key not in self.broker.pending_orders:
                return {"ok": False, "error": "No such pending order."}
            order = self.broker.cancel_pending_order(symbol_key)
            self._log_event(order.trade_id, "CANCELLED", f"GTT order for {symbol_key} cancelled")
            return {"ok": True}

    def close_manual(self, symbol_key: str) -> dict:
        with self.lock:
            if symbol_key not in self.broker.positions:
                return {"ok": False, "error": "No such open position."}
            ltp = self.ltp_map.get(symbol_key)
            if ltp is None:
                return {"ok": False, "error": "No current price available for that instrument."}
            trade = self.broker.close_position(symbol_key, ltp, ExitReason.MANUAL)
            self._forget_position(symbol_key)
            self._log_event(trade.trade_id, "EXITED", f"Manual exit @ {ltp:.2f}, pnl {trade.pnl:.2f}")
            return {"ok": True, "pnl": trade.pnl}

    # ---- read-only snapshots for the API layer -----------------------------

    def get_positions(self) -> list[dict]:
        with self.lock:
            out = []
            for key, pos in self.broker.positions.items():
                ltp = self.ltp_map.get(key)
                out.append({
                    "trade_id": pos.trade_id,
                    "symbol": key,
                    "side": pos.side.value,
                    "qty": pos.qty,
                    "entry_price": pos.entry_price,
                    "stop_loss": pos.stop_loss,
                    "target": pos.target,
                    "ltp": ltp,
                    "unrealized_pnl": pos.unrealized_pnl(ltp) if ltp is not None else None,
                    "status": "OPEN",
                })
            for key, order in self.broker.pending_orders.items():
                out.append({
                    "trade_id": order.trade_id,
                    "symbol": key,
                    "side": order.side.value,
                    "qty": order.qty,
                    "entry_price": order.trigger_price,
                    "stop_loss": order.stop_loss,
                    "target": order.target,
                    "ltp": self.ltp_map.get(key),
                    "unrealized_pnl": None,
                    "status": "PENDING_ORDER",
                })
            return out

    def get_order_events(self, since_id: int = 0) -> list[dict]:
        with self.lock:
            return [e for e in self.order_events if e["id"] > since_id]

    def get_violations(self) -> list[dict]:
        return list(self.risk.violation_log)

    def get_budget_status(self) -> dict:
        with self.lock:
            current_equity = self.config.starting_capital + self.broker.mark_to_market(self.ltp_map)
            open_count = len(self.broker.positions)
            market_open_now = self.market_open_now
        status = self.risk.budget_status(current_equity)
        status["open_positions"] = open_count
        status["market_open_now"] = market_open_now
        return status

    def update_capital(self, new_capital: float) -> None:
        """User-editable trading capital. Shifts the baseline by the delta
        rather than resetting it, so today's realized P&L isn't discarded -
        e.g. going from 1,00,000 to 1,50,000 with +2,000 realized so far
        leaves cash at 1,52,000, not 1,50,000."""
        with self.lock:
            delta = new_capital - self.config.starting_capital
            self.config.starting_capital = new_capital
            self.broker.starting_capital = new_capital
            self.broker.cash += delta
            self.risk.day_start_equity += delta
