"""Pure strategy logic - no API calls, so it can be tested offline.

Rules
-----
CE entry: spot > spot range HIGH (green line)  AND  ATM CE premium > CE range HIGH
PE entry: spot < spot range LOW  (red line)    AND  ATM PE premium > PE range HIGH
If only spot breaks (premium doesn't) or only premium breaks (spot doesn't) -> no trade.

Exit: SL 20 premium points (SL_MODE="fixed"), trailing SL in steps of R (R = initial risk),
optional fixed 1:2 target, or square-off at SQUARE_OFF_TIME.

Trailing (defaults): profit +1R -> SL to cost, +2R -> SL locks +1R, +3R -> SL locks +2R ...
"""
import math
from dataclasses import dataclass, field
from typing import Optional

import config


@dataclass
class Range:
    high: float
    low: float


@dataclass
class Position:
    side: str            # "CE" or "PE"
    entry: float         # premium at entry
    ref_entry: float     # tracked price at entry (premium, or spot in spot mode)
    direction: int       # +1 profit when tracked price rises, -1 when it falls
    risk: float          # 1R, in tracked-price points
    sl: float            # current SL level on the tracked price
    target: Optional[float]
    entry_time: object
    spot_at_entry: float
    lock_r: Optional[float] = None   # R currently locked by the trailing SL


@dataclass
class ORBStrategy:
    spot: Range
    ce: Range
    pe: Range
    buffer: float = config.BREAKOUT_BUFFER
    rr: float = config.RISK_REWARD
    sl_mode: str = config.SL_MODE
    sl_points: float = config.SL_POINTS
    use_target: bool = config.USE_TARGET
    trail: bool = config.TRAIL_ENABLED
    trail_start_r: float = config.TRAIL_START_R
    trail_lock_at_start_r: float = config.TRAIL_LOCK_AT_START_R
    trail_step_r: float = config.TRAIL_STEP_R
    max_trades: int = config.MAX_TRADES_PER_DAY
    position: Optional[Position] = None
    trades_taken: int = 0
    closed: list = field(default_factory=list)

    # ---------- signals ----------
    def ce_signal(self, spot, ce):
        return spot > self.spot.high + self.buffer and ce > self.ce.high + self.buffer

    def pe_signal(self, spot, pe):
        return spot < self.spot.low - self.buffer and pe > self.pe.high + self.buffer

    # ---------- levels ----------
    def _open(self, side, premium, spot, now):
        if self.sl_mode == "spot":
            ref, d = spot, (1 if side == "CE" else -1)
            sl = self.spot.low if side == "CE" else self.spot.high
            risk = abs(spot - sl)
        else:
            ref, d = premium, 1
            if self.sl_mode == "fixed":
                risk = self.sl_points
            else:
                risk = premium - (self.ce if side == "CE" else self.pe).low
            sl = premium - risk
        target = round(ref + d * self.rr * risk, 2) if self.use_target else None
        return Position(side, premium, ref, d, risk, round(sl, 2), target, now, spot)

    def _tracked(self, spot, premium):
        return spot if self.sl_mode == "spot" else premium

    def _update_trail(self, px):
        """Ratchet the SL up (never down) as profit reaches each R step."""
        p = self.position
        gain_r = p.direction * (px - p.ref_entry) / p.risk
        if gain_r < self.trail_start_r:
            return None
        steps = math.floor((gain_r - self.trail_start_r) / self.trail_step_r + 1e-9)
        lock_r = self.trail_lock_at_start_r + steps * self.trail_step_r
        if p.lock_r is not None and lock_r <= p.lock_r:
            return None
        new_sl = round(p.ref_entry + p.direction * lock_r * p.risk, 2)
        if p.direction * (new_sl - p.sl) <= 0:
            return None
        p.sl, p.lock_r = new_sl, lock_r
        return new_sl

    def _exit_reason(self, now, px):
        p = self.position
        if now.time() >= config.SQUARE_OFF_TIME:
            return "SQUARE_OFF"
        if p.direction * (px - p.sl) <= 0:
            return "SL" if p.lock_r is None else "TRAIL_SL"
        if p.target is not None and p.direction * (px - p.target) >= 0:
            return "TARGET"
        return None

    # ---------- main hook ----------
    def on_tick(self, now, spot, ce, pe):
        """Feed latest LTPs. Returns a list of action dicts for the broker."""
        actions = []

        if self.position:
            p = self.position
            premium = ce if p.side == "CE" else pe
            px = self._tracked(spot, premium)
            reason = self._exit_reason(now, px)
            if reason:
                pnl = round(premium - p.entry, 2)
                actions.append({"action": "EXIT", "side": p.side, "price": premium,
                                "reason": reason, "pnl_points": pnl, "spot": spot})
                self.closed.append((p, premium, reason, pnl))
                self.position = None
            elif self.trail:
                new_sl = self._update_trail(px)
                if new_sl is not None:
                    actions.append({"action": "TRAIL", "side": p.side, "price": premium,
                                    "sl": new_sl, "reason": f"LOCK_{p.lock_r:g}R", "spot": spot})
            return actions

        if self.trades_taken >= self.max_trades:
            return actions
        if now.time() >= config.NO_NEW_ENTRY_AFTER:
            return actions

        side = None
        if self.ce_signal(spot, ce):
            side, premium = "CE", ce
        elif self.pe_signal(spot, pe):
            side, premium = "PE", pe

        if side:
            self.position = p = self._open(side, premium, spot, now)
            self.trades_taken += 1
            actions.append({"action": "ENTRY", "side": side, "price": premium,
                            "sl": p.sl, "target": p.target, "sl_mode": self.sl_mode, "spot": spot})
        return actions
