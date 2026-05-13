"""Audit-Iter 15 (2026-05-12): compute_position_size multiplier stacking.

Bug PSZ-9 (HIGH): Negative oder zero account_equity wurde silent ignoriert
  (`if account_equity and account_equity > 0` ist falsy für 0 und negative).
  Folge: max_shares blieb beim MAX_LOSS_PER_TRADE_USD/risk_per_share-Limit,
  also ~1000 Shares, OBWOHL das Konto in Margin-Call war. Bot tradete
  munter weiter und produzierte rejected orders.

Plus: explizite Tests für Multiplier-Stacking (quarter-size, power-hour,
liquidity-cap, SPY-multiplier am call-site).
"""
from __future__ import annotations
import sys
from datetime import time as dtime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug PSZ-9: Broken Account ───────────────────────────────────────────────
def test_negative_equity_returns_zero():
    """Margin-Call-Konto darf NICHT traden."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    assert compute_position_size(10.0, 9.95, -1000.0, day) == 0


def test_zero_equity_returns_zero():
    """Paper-Reset-Konto (equity=0) darf NICHT traden."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    assert compute_position_size(10.0, 9.95, 0.0, day) == 0


def test_positive_equity_proceeds():
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    n = compute_position_size(10.0, 9.95, 10000.0, day)
    assert n > 0


# ─── Multiplier-Stacking ─────────────────────────────────────────────────────
def test_quarter_size_halves_quartile_shares():
    """quarter_size_unlocked=False muss shares // 4."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    full = compute_position_size(10.0, 9.95, 10000.0, day)
    day.quarter_size_unlocked = False
    quarter = compute_position_size(10.0, 9.95, 10000.0, day)
    assert quarter == full // 4


def test_power_hour_full_after_hour_reduced():
    """Vor POWER_HOUR_END: full mult, danach: 0.75x."""
    from bot import compute_position_size, DayState, POWER_HOUR_END
    day = DayState()
    day.quarter_size_unlocked = True
    full = compute_position_size(10.0, 9.95, 100000.0, day,
                                   ny_time=dtime(10, 0))  # pre-end
    after = compute_position_size(10.0, 9.95, 100000.0, day,
                                    ny_time=dtime(11, 0))  # post-end
    assert after < full
    assert after == int(full * 0.75) or abs(after - int(full * 0.75)) <= 1


def test_liquidity_cap_clamps_shares():
    """avg_volume klein → liquidity cap hart."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    # avg_volume=1000, 1% liquidity cap = 10 shares max
    n = compute_position_size(10.0, 9.95, 100000.0, day, avg_volume=1000)
    assert n <= 10


def test_no_liquidity_param_uses_only_risk_cap():
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    n_with = compute_position_size(10.0, 9.95, 100000.0, day, avg_volume=1000)
    n_without = compute_position_size(10.0, 9.95, 100000.0, day, avg_volume=None)
    assert n_without > n_with


# ─── 1%-Equity-Risk-Rule ─────────────────────────────────────────────────────
def test_equity_cap_overrides_max_loss_when_smaller():
    """Kleiner Account: 1%-Cap dominiert vs MAX_LOSS_PER_TRADE_USD."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    # $500 equity, 1% = $5 risk allowance, risk_per_share = $0.50
    # → max 10 shares (kleiner als MAX_LOSS/0.50 = 50)
    n = compute_position_size(10.0, 9.50, 500.0, day)
    assert n <= 10


def test_equity_cap_irrelevant_when_max_loss_smaller():
    """Großer Account: MAX_LOSS_PER_TRADE_USD dominiert."""
    from bot import compute_position_size, DayState, MAX_LOSS_PER_TRADE_USD
    day = DayState()
    day.quarter_size_unlocked = True
    n = compute_position_size(10.0, 9.95, 1_000_000.0, day)
    # MAX_LOSS / risk_per_share = MAX_LOSS / 0.05
    expected_max = int(MAX_LOSS_PER_TRADE_USD / 0.05)
    assert n <= expected_max


# ─── Defensive: pathological inputs ──────────────────────────────────────────
def test_entry_equals_stop_returns_zero():
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    assert compute_position_size(10.0, 10.0, 10000.0, day) == 0


def test_entry_below_stop_returns_zero():
    """Inverted setup (entry < stop) = data error."""
    from bot import compute_position_size, DayState
    day = DayState()
    day.quarter_size_unlocked = True
    assert compute_position_size(9.0, 10.0, 10000.0, day) == 0


def test_min_stop_005_prevents_huge_position():
    """raw_risk = 0.001 → forced to 0.05 → keine 50000-Share-Position."""
    from bot import compute_position_size, DayState, MAX_LOSS_PER_TRADE_USD
    day = DayState()
    day.quarter_size_unlocked = True
    n = compute_position_size(10.0, 9.999, 100000.0, day)
    # Mit min-stop 0.05: max_shares = MAX_LOSS_PER_TRADE_USD / 0.05
    assert n <= int(MAX_LOSS_PER_TRADE_USD / 0.05) + 1
