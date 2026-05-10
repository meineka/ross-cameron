"""Risk-Engine Tests — Position-Sizing, Daily-Caps, Spiral-Detection."""
from datetime import time as dtime
from bot import compute_position_size, can_enter_new, DayState, MAX_LOSS_PER_TRADE_USD


def test_position_size_basic():
    day = DayState(quarter_size_unlocked=True)
    n = compute_position_size(entry=5.10, stop=5.02, account_equity=25000, day=day)
    # Risk per share = 8 cents → max-loss/risk = 50/0.08 = 625 shares
    assert 600 <= n <= 650


def test_position_size_quarter_mode():
    day = DayState(quarter_size_unlocked=False)
    n = compute_position_size(entry=5.10, stop=5.02, account_equity=25000, day=day)
    # Quarter of full = 625/4 = 156
    assert 130 <= n <= 170


def test_position_size_no_room_returns_zero():
    """Stop >= Entry → 0 Shares."""
    day = DayState()
    n = compute_position_size(entry=5.0, stop=5.5, account_equity=25000, day=day)
    assert n == 0


def test_can_enter_blocked_after_daily_max_loss():
    day = DayState(realized_pnl=-200)   # > $150 max
    ok, reason = can_enter_new(day, dtime(10, 0))
    assert ok is False
    assert "daily_max_loss" in reason


def test_can_enter_blocked_after_1130():
    day = DayState()
    ok, reason = can_enter_new(day, dtime(11, 35))
    assert ok is False
    assert "1130" in reason


def test_can_enter_blocked_before_rth():
    day = DayState()
    ok, reason = can_enter_new(day, dtime(9, 15))
    assert ok is False
    assert "rth" in reason.lower()


def test_can_enter_blocked_when_spiral_locked():
    day = DayState(spiral_locked=True)
    ok, reason = can_enter_new(day, dtime(10, 0))
    assert ok is False
    assert "spiral" in reason


def test_can_enter_blocked_intraday_drawdown():
    """Wenn 50% des Tagespeak zurückgegeben → block."""
    day = DayState(peak_pnl=200, realized_pnl=80)   # gave back 60% of peak
    ok, reason = can_enter_new(day, dtime(10, 0))
    assert ok is False
    assert "drawdown" in reason


def test_can_enter_ok_in_window():
    day = DayState(realized_pnl=50)
    ok, reason = can_enter_new(day, dtime(10, 0))
    assert ok is True
    assert reason == ""
