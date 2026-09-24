"""
Enforces system-level risk rules on top of whatever entries the strategy
engine proposes: position sizing by risk %, daily loss cap, max trades/day,
max concurrent positions, and trading-hours guard.

The strategy engine decides *what* to trade and where SL/target sit; this
module decides *whether* a signal is allowed through right now and *how
big* the position should be.
"""

from __future__ import annotations

from datetime import time

from config import TradingConfig
from ist_clock import now_ist, today_ist


class RiskManager:
    def __init__(self, config: TradingConfig):
        self.config = config
        self.trades_today = 0
        self.day_start_equity = config.starting_capital
        self._current_date = today_ist()
        self.violation_log: list[dict] = []

    def _roll_day_if_needed(self, current_equity: float) -> None:
        today = today_ist()
        if today != self._current_date:
            self._current_date = today
            self.trades_today = 0
            self.day_start_equity = current_equity

    def _tradable_capital(self) -> float:
        """Only the deployed half (by default) of today's starting equity
        is at risk - the rest is reserve, per §2."""
        return self.day_start_equity * self.config.tradable_capital_pct / 100

    def within_trading_hours(self, now: time | None = None) -> bool:
        now = now or now_ist().time()
        return self.config.market_open <= now <= self.config.no_trade_after

    def should_square_off(self, now: time | None = None) -> bool:
        now = now or now_ist().time()
        return now >= self.config.square_off_time

    def can_open_new_trade(self, current_equity: float, open_position_count: int,
                             symbol: str | None = None) -> tuple[bool, str]:
        self._roll_day_if_needed(current_equity)

        reason = None
        if not self.within_trading_hours():
            reason = "outside trading hours"
        elif open_position_count >= self.config.max_concurrent_positions:
            reason = "max concurrent positions reached"
        elif self.trades_today >= self.config.max_trades_per_day:
            reason = "max trades per day reached"
        else:
            tradable = self._tradable_capital()
            loss_so_far = max(0.0, self.day_start_equity - current_equity)
            daily_loss_pct = (loss_so_far / tradable * 100) if tradable else 0.0
            if daily_loss_pct >= self.config.max_daily_loss_pct:
                reason = f"max daily loss hit ({daily_loss_pct:.2f}% of deployed capital)"

        if reason is not None:
            self.violation_log.append({
                "time": now_ist().isoformat(timespec="seconds"),
                "symbol": symbol or "-",
                "reason": reason,
            })
            return False, reason
        return True, ""

    def budget_status(self, current_equity: float) -> dict:
        """The "Oxygen" gauge: how much of today's loss allowance (on the
        deployed/tradable capital - §2) is left, and whether new trades are
        currently allowed at all."""
        self._roll_day_if_needed(current_equity)
        tradable = self._tradable_capital()
        max_loss_amount = tradable * self.config.max_daily_loss_pct / 100
        used_amount = max(0.0, self.day_start_equity - current_equity)
        remaining_amount = max(0.0, max_loss_amount - used_amount)
        pct_remaining = round(remaining_amount / max_loss_amount * 100, 1) if max_loss_amount else 0.0
        return {
            "oxygen_pct": pct_remaining,
            "oxygen_remaining": round(remaining_amount, 2),
            "trades_used": self.trades_today,
            "trades_max": self.config.max_trades_per_day,
            "safe_to_trade": remaining_amount > 0 and self.within_trading_hours(),
            "per_trade_budget": round(tradable * self.config.risk_per_trade_pct / 100, 2),
            "tradable_capital": round(tradable, 2),
        }

    def position_size(self, entry_price: float, stop_loss: float, lot_size: int, size_multiplier: float = 1.0) -> int:
        """Return quantity (in whole lots) sized so that a full SL hit risks
        approximately risk_per_trade_pct of the DEPLOYED/tradable capital
        (§2 - half the account stays in reserve), not the whole account.
        lot_size should come from the live instrument data (it changes
        periodically), not a hardcoded constant. size_multiplier scales this
        down for the strategy's "small quantity" windows (e.g. 0.5) - still
        floored at one lot."""
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            raise ValueError("entry_price and stop_loss must differ to size a position")
        if lot_size <= 0:
            raise ValueError(f"Invalid lot_size {lot_size!r}")

        risk_amount = self._tradable_capital() * self.config.risk_per_trade_pct / 100 * size_multiplier
        raw_qty = risk_amount / risk_per_unit
        lots = max(1, round(raw_qty / lot_size))
        return lots * lot_size

    def record_trade_opened(self) -> None:
        self.trades_today += 1
