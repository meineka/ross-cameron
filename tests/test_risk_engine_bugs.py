"""Latente Risk-Engine-Bugs aus dem 2. Audit-Pass."""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import time as dtime

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug A: account_equity wird nicht benutzt ────────────────────────────────
def test_equity_cap_limits_position_when_account_small():
    """Cameron-Rule: max 1 % of equity at risk pro Trade.
    Bei Equity $1 000 sollten max $10 riskiert werden, nicht $50.
    """
    import bot
    d = bot.DayState()
    d.quarter_size_unlocked = True
    # Entry $10, Stop $9.50 → risk $0.50/share, max-loss-policy $50 → 100 shares
    # Aber Equity nur $1 000: 1 % = $10 → max $10/0.50 = 20 shares
    shares = bot.compute_position_size(
        entry=10.0, stop=9.50, account_equity=1_000,
        day=d, ny_time=dtime(9, 45),
    )
    # Mit Bug: 100 shares. Mit Fix: ≤20 shares
    assert shares <= 20, f"shares {shares} exceed 1% of equity rule"


# ─── Bug B: winziger risk_per_share = explodierende Position ─────────────────
def test_tiny_risk_per_share_capped():
    """Bei risk/share <1c sollte Position auf vernünftiges Niveau gecapped sein."""
    import bot
    d = bot.DayState()
    d.quarter_size_unlocked = True
    shares = bot.compute_position_size(
        entry=10.00, stop=9.999, account_equity=100_000,
        day=d, ny_time=dtime(9, 45),
    )
    # Ohne Cap: $50/$0.001 = 50 000 Shares × $10 = $500k Position!
    # Mit Cap: sollte auf ~10k Shares oder weniger limited sein
    assert shares < 10_000, f"shares {shares} ist absurd hoch — entry/stop zu nah"


def test_zero_risk_per_share_returns_zero():
    import bot
    d = bot.DayState()
    d.quarter_size_unlocked = True
    shares = bot.compute_position_size(
        entry=10.0, stop=10.0, account_equity=100_000,
        day=d, ny_time=dtime(9, 45),
    )
    assert shares == 0


# ─── Bug C: negative max_shares-Pfad ─────────────────────────────────────────
def test_no_negative_shares_returned():
    """Floor-Division könnte mit absurden Inputs negativ werden.
    compute_position_size soll IMMER ≥ 0 zurückgeben.
    """
    import bot
    d = bot.DayState()
    d.quarter_size_unlocked = False
    # Sehr großer Stop-Abstand → wenige Shares, mit //4 fast 0
    shares = bot.compute_position_size(
        entry=10.0, stop=0.01, account_equity=100_000,
        day=d, ny_time=dtime(9, 45),
    )
    assert shares >= 0


def test_negative_entry_returns_zero():
    """Defensive: Wenn entry≤0 oder stop≤0 → 0 shares."""
    import bot
    d = bot.DayState()
    d.quarter_size_unlocked = True
    # Invalid inputs
    assert bot.compute_position_size(-5, -10, 100_000, d, ny_time=dtime(9, 45)) == 0
    assert bot.compute_position_size(0, -1, 100_000, d, ny_time=dtime(9, 45)) == 0


# ─── can_enter_new edge cases ────────────────────────────────────────────────
def test_can_enter_blocks_after_hard_flat():
    """Sicherheit: nach 11:30 ET kein neuer Entry."""
    import bot
    d = bot.DayState(); d.spy_size_multiplier = 1.0
    ok, reason = bot.can_enter_new(d, dtime(11, 31))
    assert ok is False
    assert reason == "after_1130"


def test_can_enter_blocks_intraday_drawdown_50pct():
    """Wenn realized_pnl unter 50 % vom Peak fällt → block."""
    import bot
    d = bot.DayState()
    d.spy_size_multiplier = 1.0
    d.peak_pnl = 100.0
    d.realized_pnl = 40.0  # 60 % drawdown vom peak
    ok, reason = bot.can_enter_new(d, dtime(10, 0))
    assert ok is False
    assert "drawdown" in reason


def test_can_enter_at_exact_boundary_times():
    """Boundary: 09:30:00 → before_rth blockt (NEW_ENTRIES_START 09:35)."""
    import bot
    d = bot.DayState(); d.spy_size_multiplier = 1.0
    # 9:30:00 = TIME_RTH_START (nicht <) → fällt auf open_range_5min check
    ok, reason = bot.can_enter_new(d, dtime(9, 30))
    assert ok is False
    assert reason == "open_range_5min"


def test_can_enter_max_trades_per_day_enforced():
    import bot
    d = bot.DayState(); d.spy_size_multiplier = 1.0
    d.trades_completed_today = bot.MAX_TRADES_PER_DAY
    ok, reason = bot.can_enter_new(d, dtime(10, 0))
    assert ok is False
    assert "max" in reason
