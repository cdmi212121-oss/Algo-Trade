"""Paper broker - records trades only. NO real orders are ever sent to Angel One.

Hook your own paper-trading simulator into `_send_to_simulator` below.
"""
import csv
import logging
from datetime import datetime

import config

log = logging.getLogger("paper")


class PaperBroker:
    def __init__(self):
        config.TRADE_LOG_DIR.mkdir(exist_ok=True)
        day = datetime.now(config.IST).strftime("%Y%m%d")
        self.path = config.TRADE_LOG_DIR / f"trades_{day}.csv"
        new = not self.path.exists()
        self._f = open(self.path, "a", newline="")
        self._w = csv.writer(self._f)
        if new:
            self._w.writerow(["time", "action", "side", "symbol", "token", "qty",
                              "price", "sl", "target", "reason", "pnl_points", "pnl_rs", "spot"])

    def execute(self, act, symbol, token, qty):
        now = datetime.now(config.IST).strftime("%Y-%m-%d %H:%M:%S")
        pnl_rs = round(act["pnl_points"] * qty, 2) if "pnl_points" in act else ""
        target = act.get("target")
        row = [now, act["action"], act["side"], symbol, token, qty, act["price"],
               act.get("sl", ""), "" if target is None else target, act.get("reason", ""),
               act.get("pnl_points", ""), pnl_rs, act["spot"]]
        self._w.writerow(row)
        self._f.flush()
        if act["action"] == "ENTRY":
            side = "BUY"
            detail = f"SL {act['sl']} TGT {target if target is not None else 'trailing'}"
        elif act["action"] == "TRAIL":
            side = "MODIFY_SL"
            detail = f"SL moved to {act['sl']} ({act['reason']})"
        else:
            side = "SELL"
            detail = f"{act['reason']} P&L {act['pnl_points']} pts / Rs {pnl_rs}"
        log.info("[PAPER] %s %s x%d @ %.2f %s", side, symbol, qty, act["price"], detail)
        self._send_to_simulator(side, symbol, token, qty, act)

    def _send_to_simulator(self, side, symbol, token, qty, act):
        """Connect your paper-trading simulator here. side is BUY, SELL or MODIFY_SL, e.g.:
            if side == "MODIFY_SL":
                simulator.modify_sl(symbol=symbol, sl=act["sl"])
            else:
                simulator.place_order(symbol=symbol, side=side, qty=qty, price=act["price"])
        """
        pass

    def close(self):
        self._f.close()
