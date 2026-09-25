"""Offline checks of the entry/exit rules. Run: python test_strategy.py"""
from datetime import datetime

import config
from strategy import ORBStrategy, Range


def t(h, m):
    return datetime(2026, 9, 24, h, m, tzinfo=config.IST)


def new(**kw):
    # spot 25000-25050, CE 100-120, PE 90-110
    opts = dict(sl_mode="premium", use_target=True, trail=False)
    opts.update(kw)
    return ORBStrategy(Range(25050, 25000), Range(120, 100), Range(110, 90), **opts)


def test_spot_only_break_no_trade():
    assert new().on_tick(t(10, 0), 25060, 118, 80) == []      # spot up, CE premium not above 120


def test_premium_only_break_no_trade():
    assert new().on_tick(t(10, 0), 25040, 125, 80) == []      # CE premium up, spot not above 25050


def test_ce_entry_and_target():
    s = new()
    [a] = s.on_tick(t(10, 0), 25060, 125, 80)
    assert a["action"] == "ENTRY" and a["side"] == "CE"
    assert a["sl"] == 100 and a["target"] == 175               # risk 25 -> target 125 + 50
    assert s.on_tick(t(10, 5), 25080, 150, 70) == []
    [x] = s.on_tick(t(10, 10), 25100, 176, 60)
    assert x["reason"] == "TARGET" and x["pnl_points"] == 51


def test_pe_entry_and_sl():
    s = new()
    [a] = s.on_tick(t(10, 0), 24990, 90, 115)
    assert a["side"] == "PE" and a["sl"] == 90 and a["target"] == 165
    [x] = s.on_tick(t(10, 5), 25010, 110, 89)
    assert x["reason"] == "SL"


def test_max_one_trade_and_square_off():
    s = new()
    s.on_tick(t(10, 0), 25060, 125, 80)
    [x] = s.on_tick(t(15, 15), 25060, 130, 80)
    assert x["reason"] == "SQUARE_OFF"
    assert s.on_tick(t(15, 16), 25100, 200, 80) == []          # no second trade


def test_spot_sl_mode():
    s = new(sl_mode="spot")
    [a] = s.on_tick(t(10, 0), 25060, 125, 80)
    assert a["sl"] == 25000 and a["target"] == 25180           # risk 60 spot pts -> +120
    [x] = s.on_tick(t(10, 5), 24999, 95, 120)
    assert x["reason"] == "SL"


def test_fixed_20_point_sl():
    s = new(sl_mode="fixed", sl_points=20)
    [a] = s.on_tick(t(10, 0), 25060, 125, 80)
    assert a["sl"] == 105 and a["target"] == 165               # 125 - 20, 125 + 40
    assert s.on_tick(t(10, 5), 25055, 106, 85) == []
    [x] = s.on_tick(t(10, 6), 25050, 105, 86)
    assert x["reason"] == "SL" and x["pnl_points"] == -20


def test_trailing_rides_to_3x():
    s = new(sl_mode="fixed", sl_points=20, use_target=False, trail=True)
    [a] = s.on_tick(t(10, 0), 25060, 125, 80)
    assert a["sl"] == 105 and a["target"] is None
    assert s.on_tick(t(10, 1), 25070, 140, 80) == []           # +15, below 1R
    [m] = s.on_tick(t(10, 2), 25080, 145, 80)                  # +20 = 1R -> SL to cost
    assert m["action"] == "TRAIL" and m["sl"] == 125
    [m] = s.on_tick(t(10, 3), 25100, 166, 80)                  # +41 = 2R -> lock +1R
    assert m["sl"] == 145
    assert s.on_tick(t(10, 4), 25090, 155, 80) == []           # pullback: SL never moves down
    [m] = s.on_tick(t(10, 5), 25150, 190, 80)                  # +65 = 3R -> lock +2R
    assert m["sl"] == 165
    [m] = s.on_tick(t(10, 6), 25200, 206, 80)                  # +81 = 4R -> lock +3R
    assert m["sl"] == 185
    [x] = s.on_tick(t(10, 7), 25170, 184, 80)                  # falls back to trailed SL
    assert x["reason"] == "TRAIL_SL" and x["pnl_points"] == 59  # ~3x the 20-pt risk


def test_trailing_breakeven_exit():
    s = new(sl_mode="fixed", sl_points=20, use_target=False, trail=True)
    s.on_tick(t(10, 0), 24990, 80, 115)                        # PE entry at 115
    [m] = s.on_tick(t(10, 1), 24970, 80, 136)
    assert m["sl"] == 115
    [x] = s.on_tick(t(10, 2), 24995, 80, 115)
    assert x["reason"] == "TRAIL_SL" and x["pnl_points"] == 0


def test_no_entry_after_cutoff():
    assert new().on_tick(t(14, 31), 25060, 125, 80) == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
