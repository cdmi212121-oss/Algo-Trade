"""
Paper trading engine: simulates order fills, position tracking, and P&L
against live prices, without ever sending a real order anywhere.

Kept deliberately independent of any specific strategy or data source so it
can be reused as-is once the actual scalping rules (from the course videos)
are encoded in strategy_engine.py.
"""

from __future__ import annotations

import csv
import os
import random
import string
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from ist_clock import now_ist


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    GTT = "GTT"


class ExitReason(Enum):
    TARGET = "TARGET"
    STOPLOSS = "STOPLOSS"
    TRAIL_SL = "TRAIL_SL"
    MANUAL = "MANUAL"
    EOD_SQUAREOFF = "EOD_SQUAREOFF"
    PARTIAL_SCALE_OUT = "PARTIAL_SCALE_OUT"


def _generate_trade_id() -> str:
    return "TM" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))


@dataclass
class Position:
    symbol: str          # instrument key, e.g. "NIFTY_NIFTY_25SEP2026_23500_CE"
    side: Side
    qty: int
    entry_price: float
    entry_time: datetime
    stop_loss: float
    target: float
    trail_sl_step: Optional[float] = None  # if set, SL trails by this much per favorable move
    trade_id: str = field(default_factory=_generate_trade_id)
    highest_favorable_price: float = field(init=False)

    def __post_init__(self):
        self.highest_favorable_price = self.entry_price

    def unrealized_pnl(self, ltp: float) -> float:
        direction = 1 if self.side == Side.BUY else -1
        return direction * (ltp - self.entry_price) * self.qty

    def update_trailing_sl(self, ltp: float) -> None:
        if self.trail_sl_step is None:
            return
        direction = 1 if self.side == Side.BUY else -1
        favorable_move = direction * (ltp - self.highest_favorable_price)
        if favorable_move > 0:
            self.highest_favorable_price = ltp
            self.stop_loss += direction * self.trail_sl_step

    def check_exit(self, ltp: float) -> Optional[ExitReason]:
        if self.side == Side.BUY:
            if ltp <= self.stop_loss:
                return ExitReason.STOPLOSS
            if ltp >= self.target:
                return ExitReason.TARGET
        else:
            if ltp >= self.stop_loss:
                return ExitReason.STOPLOSS
            if ltp <= self.target:
                return ExitReason.TARGET
        return None


@dataclass
class PendingOrder:
    """A GTT order: sits inactive until the underlying instrument's LTP
    crosses trigger_price, then converts into a real Position (subject to
    a fresh risk check at fill time - the account's risk state may have
    changed while it sat pending)."""
    symbol: str
    side: Side
    qty: int
    trigger_price: float
    stop_loss: float
    target: float
    placed_time: datetime = field(default_factory=now_ist)
    trade_id: str = field(default_factory=_generate_trade_id)

    def should_fill(self, ltp: float) -> bool:
        # BUY GTT: trigger fires once price falls to/through the trigger
        # (a better/equal entry price), matching typical GTT "buy on dip"
        # semantics. SELL is the mirror case.
        return ltp <= self.trigger_price if self.side == Side.BUY else ltp >= self.trigger_price


@dataclass
class TradeRecord:
    symbol: str
    side: Side
    qty: int
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    exit_reason: ExitReason
    pnl: float
    trade_id: str = field(default_factory=_generate_trade_id)


class PaperBroker:
    def __init__(self, starting_capital: float = 100_000.0, log_dir: str = "logs"):
        self.cash = starting_capital
        self.starting_capital = starting_capital
        self.positions: dict[str, Position] = {}
        self.pending_orders: dict[str, PendingOrder] = {}
        self.trade_log: list[TradeRecord] = []
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._log_path = os.path.join(log_dir, f"trades_{now_ist():%Y%m%d}.csv")
        self._ensure_log_header()

    def _ensure_log_header(self) -> None:
        if not os.path.exists(self._log_path):
            with open(self._log_path, "w", newline="") as f:
                csv.writer(f).writerow(
                    ["trade_id", "symbol", "side", "qty", "entry_price", "entry_time",
                     "exit_price", "exit_time", "exit_reason", "pnl"]
                )

    def open_position(
        self,
        symbol: str,
        side: Side,
        qty: int,
        entry_price: float,
        stop_loss: float,
        target: float,
        trail_sl_step: Optional[float] = None,
        trade_id: Optional[str] = None,
    ) -> Position:
        if symbol in self.positions:
            raise ValueError(f"Position already open for {symbol}")
        pos = Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=entry_price,
            entry_time=now_ist(),
            stop_loss=stop_loss,
            target=target,
            trail_sl_step=trail_sl_step,
            **({"trade_id": trade_id} if trade_id else {}),
        )
        self.positions[symbol] = pos
        return pos

    def place_pending_order(self, symbol: str, side: Side, qty: int, trigger_price: float,
                              stop_loss: float, target: float) -> PendingOrder:
        if symbol in self.pending_orders or symbol in self.positions:
            raise ValueError(f"An order or position already exists for {symbol}")
        order = PendingOrder(symbol=symbol, side=side, qty=qty, trigger_price=trigger_price,
                              stop_loss=stop_loss, target=target)
        self.pending_orders[symbol] = order
        return order

    def cancel_pending_order(self, symbol: str) -> PendingOrder:
        return self.pending_orders.pop(symbol)

    def check_pending_orders(self, ltp_by_symbol: dict[str, float]) -> list[Position]:
        """Call every tick. Converts any triggered GTT order into a real
        position at the current LTP. Caller is responsible for re-checking
        risk before/after - see engine.py."""
        filled = []
        for symbol in list(self.pending_orders.keys()):
            ltp = ltp_by_symbol.get(symbol)
            if ltp is None:
                continue
            order = self.pending_orders[symbol]
            if order.should_fill(ltp):
                del self.pending_orders[symbol]
                filled.append(self.open_position(
                    symbol=symbol, side=order.side, qty=order.qty, entry_price=ltp,
                    stop_loss=order.stop_loss, target=order.target, trade_id=order.trade_id,
                ))
        return filled

    def close_position(self, symbol: str, exit_price: float, reason: ExitReason) -> TradeRecord:
        pos = self.positions.pop(symbol)
        pnl = pos.unrealized_pnl(exit_price)
        self.cash += pnl
        record = TradeRecord(
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=now_ist(),
            exit_reason=reason,
            pnl=pnl,
            trade_id=pos.trade_id,
        )
        self.trade_log.append(record)
        self._append_log(record)
        return record

    def partial_close(self, symbol: str, qty: int, exit_price: float, reason: ExitReason) -> TradeRecord:
        """Book part of a position (the momentum scale-out rule: exit most
        of the quantity on a consolidation signal, let the rest ride).
        Closes the whole position instead if qty >= what's actually open."""
        pos = self.positions[symbol]
        if qty >= pos.qty:
            return self.close_position(symbol, exit_price, reason)

        pnl = pos.unrealized_pnl(exit_price) * (qty / pos.qty)
        self.cash += pnl
        pos.qty -= qty

        record = TradeRecord(
            symbol=pos.symbol,
            side=pos.side,
            qty=qty,
            entry_price=pos.entry_price,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=now_ist(),
            exit_reason=reason,
            pnl=pnl,
            trade_id=pos.trade_id,
        )
        self.trade_log.append(record)
        self._append_log(record)
        return record

    def _append_log(self, record: TradeRecord) -> None:
        with open(self._log_path, "a", newline="") as f:
            csv.writer(f).writerow(
                [record.trade_id, record.symbol, record.side.value, record.qty, record.entry_price,
                 record.entry_time, record.exit_price, record.exit_time,
                 record.exit_reason.value, round(record.pnl, 2)]
            )

    def move_sl_to_breakeven(self, symbol: str) -> None:
        pos = self.positions.get(symbol)
        if pos is not None:
            pos.stop_loss = pos.entry_price

    def check_exits(self, ltp_by_symbol: dict[str, float]) -> list[TradeRecord]:
        """Call every tick with the latest LTPs; auto-exits any position that
        has hit its (possibly trailed) stop-loss or target."""
        closed = []
        for symbol in list(self.positions.keys()):
            ltp = ltp_by_symbol.get(symbol)
            if ltp is None:
                continue
            pos = self.positions[symbol]
            pos.update_trailing_sl(ltp)
            reason = pos.check_exit(ltp)
            if reason is not None:
                closed.append(self.close_position(symbol, ltp, reason))
        return closed

    def mark_to_market(self, ltp_by_symbol: dict[str, float]) -> float:
        unrealized = sum(
            pos.unrealized_pnl(ltp_by_symbol[sym])
            for sym, pos in self.positions.items()
            if sym in ltp_by_symbol
        )
        return self.cash + unrealized - self.starting_capital

    def square_off_all(self, ltp_by_symbol: dict[str, float]) -> list[TradeRecord]:
        closed = []
        for symbol in list(self.positions.keys()):
            ltp = ltp_by_symbol.get(symbol)
            if ltp is not None:
                closed.append(self.close_position(symbol, ltp, ExitReason.EOD_SQUAREOFF))
        self.pending_orders.clear()
        return closed

    def summary(self) -> dict:
        wins = [t for t in self.trade_log if t.pnl > 0]
        losses = [t for t in self.trade_log if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in self.trade_log)
        return {
            "total_trades": len(self.trade_log),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trade_log) * 100, 1) if self.trade_log else 0.0,
            "total_pnl": round(total_pnl, 2),
            "current_cash": round(self.cash, 2),
        }
