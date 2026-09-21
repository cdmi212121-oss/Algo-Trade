"""
Central configuration for the paper trading system: capital, risk limits,
instruments, and timing. Strategy-specific thresholds (support/resistance
lookback, strike offset, SL/target %) belong in strategy_engine.py once the
actual rules are known — this file only holds system-level knobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time


@dataclass
class TradingConfig:
    starting_capital: float = 100_000.0   # user-editable at runtime via Profile - see engine.update_capital()

    # Risk management (per strategy_rules.md §2: deploy only half your
    # capital, keep the rest in reserve; daily loss cap and per-trade risk
    # are both sized off that deployed (tradable) half, not the whole account)
    tradable_capital_pct: float = 50.0
    risk_per_trade_pct: float = 2.0       # % of tradable capital risked per trade (entry-to-SL distance)
    max_daily_loss_pct: float = 11.0      # % of tradable capital - stop opening new trades once hit (§2: 10-12%)
    max_trades_per_day: int = 6
    max_concurrent_positions: int = 1      # total open positions allowed across ALL symbols combined

    # Instruments to scan
    symbols: list[str] = field(default_factory=lambda: ["NIFTY"])

    # Timing
    poll_interval_seconds: int = 5
    market_open: time = time(9, 15)
    market_close: time = time(15, 30)
    # Many scalping systems avoid the first/last few minutes (opening
    # volatility, closing auction effects) - adjust once rules are known.
    no_trade_after: time = time(15, 15)
    square_off_time: time = time(15, 20)
