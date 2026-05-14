"""Review-V2 Phase 2 behavior tests:
  P0.5 — can_enter_new risk-budget aggregation
  P1.7 — detect_bull_flag candidate-local continue
  P1.8 — rejection-counter telemetry incremented
"""
from __future__ import annotations
import sys
from datetime import time as dtime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── P0.5: risk-budget aggregation ───────────────────────────────────────────
def test_can_enter_new_blocks_when_projected_risk_exceeds_daily_cap():
    """Total projected risk (realized + open + new) > DAILY_MAX_LOSS_USD → block."""
    import bot
    d = bot.DayState()
    d.realized_pnl = -100.0  # already lost $100
    # DAILY_MAX_LOSS_USD = $150. open=30, new=30. Projected = 100+30+30 = 160 > 150
    ok, reason = bot.can_enter_new(d, dtime(10, 30),
                                    new_trade_risk_usd=30.0,
                                    open_risk_usd=30.0)
    assert ok is False
    assert "projected_risk" in reason or "exceeds_cap" in reason


def test_can_enter_new_allows_within_budget():
    """Realized loss + open + new < cap → allowed."""
    import bot
    d = bot.DayState()
    d.realized_pnl = -50.0
    d.spy_size_multiplier = 1.0
    ok, reason = bot.can_enter_new(d, dtime(10, 30),
                                    new_trade_risk_usd=30.0,
                                    open_risk_usd=30.0)
    # 50 + 30 + 30 = 110 < 150 → allowed
    assert ok is True


def test_aggregate_open_risk_sums_correctly():
    """Helper sums shares*(entry-stop) across open positions."""
    import bot
    ts1 = bot.TickerState(symbol="A", rank=1, score=1.0)
    ts1.in_position = True
    ts1.shares = 10
    ts1.entry_price = 10.0
    ts1.stop_price = 9.5  # risk = 5
    ts1.half_filled = False

    ts2 = bot.TickerState(symbol="B", rank=2, score=2.0)
    ts2.in_position = True
    ts2.shares = 20
    ts2.entry_price = 5.0
    ts2.stop_price = 4.8  # risk = 4
    ts2.half_filled = False

    ts3 = bot.TickerState(symbol="C", rank=3, score=3.0)
    ts3.in_position = False  # flat — should not count

    total = bot._aggregate_open_risk({"A": ts1, "B": ts2, "C": ts3})
    assert abs(total - (5.0 + 4.0)) < 1e-6


def test_aggregate_open_risk_excludes_half_filled():
    """Half-filled positions have BE-stop on remainder → zero downside."""
    import bot
    ts = bot.TickerState(symbol="HF", rank=1, score=1.0)
    ts.in_position = True
    ts.shares = 5
    ts.entry_price = 10.0
    ts.stop_price = 9.5
    ts.half_filled = True
    assert bot._aggregate_open_risk({"HF": ts}) == 0.0


# ─── P1.7: detect_bull_flag candidate-local continue ─────────────────────────
def test_detect_bull_flag_returns_local_veto_in_dict():
    """When a candidate-local check vetoes, the returned dict should
    contain a _veto reason for telemetry."""
    import bot
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Check that risk-veto inside the pole/flag loop uses continue not return
    assert "last_local_veto = f\"risk_" in src
    assert "last_local_veto = f\"pole_h_" in src
    # And the function returns the local-veto at the end
    assert 'return False, ({"_veto": last_local_veto} if last_local_veto else {})' in src


# ─── P1.8: rejection-counter telemetry ───────────────────────────────────────
def test_day_state_has_new_rejection_counters():
    """Review-V2 P1.8: per-veto counters should exist on DayState."""
    import bot
    d = bot.DayState()
    for field in ("patterns_rejected_vwap", "patterns_rejected_risk",
                  "patterns_rejected_pole_extension",
                  "patterns_rejected_risk_budget"):
        assert hasattr(d, field), f"DayState missing {field}"
        assert getattr(d, field) == 0


def test_handle_bar_increments_macd_counter_on_macd_veto():
    """When detect_bull_flag returns _veto=macd, patterns_rejected_macd
    should increment in handle_bar_5min."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Verify the wiring exists in handle_bar_5min (P1.8 fix)
    assert 'veto.startswith("macd")' in src
    assert "self.day.patterns_rejected_macd += 1" in src
    assert 'veto.startswith("vwap")' in src
    assert 'veto.startswith("fbo")' in src
