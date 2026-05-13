"""Audit-Iter 22 (2026-05-12): pump_dump_filter dead-code bug.

Bug PD-1 (HIGH): Bot rief pd_size_multiplier(ts.score) ohne pct+rvol
  → secondary-Filter (>100% intraday + >50x RVOL) war dead code.
  Folge: Stock mit score=8000 (unter threshold) aber pct=200% + rvol=80x
  → 1.0x size statt 0.25x. Hätte Cameron-Lesson ODYS-$17k-Loss profile
  durchgelassen.

Fix:
  - TickerState bekommt intraday_pct + rvol_proxy fields
  - premarket_scan populiert sie aus dem DataFrame
  - Bot ruft pd_size_multiplier mit allen 3 args
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Filter math: full filter rules ──────────────────────────────────────────
def test_score_above_threshold_returns_quarter():
    from pump_dump_filter import size_multiplier
    assert size_multiplier(10_001) == 0.25


def test_score_below_threshold_normal_size():
    from pump_dump_filter import size_multiplier
    assert size_multiplier(5000) == 1.0


def test_extreme_pct_and_rvol_combo_returns_quarter():
    """REGRESSION (PD-1): pct>100 + rvol>50 muss reduce auslösen
    auch wenn score < threshold."""
    from pump_dump_filter import size_multiplier
    assert size_multiplier(5000, intraday_pct=150, rvol=80) == 0.25


def test_only_extreme_pct_alone_does_not_trigger():
    """Nur pct ohne rvol triggert nicht."""
    from pump_dump_filter import size_multiplier
    assert size_multiplier(5000, intraday_pct=150, rvol=20) == 1.0


def test_only_extreme_rvol_alone_does_not_trigger():
    from pump_dump_filter import size_multiplier
    assert size_multiplier(5000, intraday_pct=50, rvol=80) == 1.0


def test_borderline_combo_does_not_trigger():
    """Genau bei threshold (pct=100, rvol=50) → noch nicht trigger
    (strict > nicht ≥)."""
    from pump_dump_filter import size_multiplier
    assert size_multiplier(5000, intraday_pct=100, rvol=50) == 1.0


def test_score_dominates_over_combo():
    """Score>threshold reicht auch ohne pct+rvol."""
    from pump_dump_filter import size_multiplier
    assert size_multiplier(20_000, intraday_pct=0, rvol=0) == 0.25


# ─── TickerState fields ──────────────────────────────────────────────────────
def test_ticker_state_has_pct_and_rvol_fields():
    """Audit-Iter 22: TickerState muss intraday_pct + rvol_proxy haben."""
    import bot
    ts = bot.TickerState(symbol="X", rank=1, score=1.0)
    assert hasattr(ts, "intraday_pct")
    assert hasattr(ts, "rvol_proxy")
    assert ts.intraday_pct == 0.0
    assert ts.rvol_proxy == 0.0


def test_ticker_state_accepts_pct_and_rvol_at_init():
    import bot
    ts = bot.TickerState(symbol="X", rank=1, score=1.0,
                          intraday_pct=120.5, rvol_proxy=40.0)
    assert ts.intraday_pct == 120.5
    assert ts.rvol_proxy == 40.0


# ─── Smoke: is_pump_dump_risk ────────────────────────────────────────────────
def test_is_pump_dump_risk_score_path():
    from pump_dump_filter import is_pump_dump_risk
    assert is_pump_dump_risk(20_000) is True
    assert is_pump_dump_risk(5000) is False


def test_is_pump_dump_risk_combo_path():
    """REGRESSION (PD-1): combo-Path muss erreichbar sein."""
    from pump_dump_filter import is_pump_dump_risk
    assert is_pump_dump_risk(5000, intraday_pct=200, rvol=80) is True


# ─── Bot-Integration: dass bot.py die neuen Felder durchreicht ───────────────
def test_bot_uses_full_pump_dump_filter():
    """Source-Check: bot.py muss pd_size_multiplier mit 3 args aufrufen."""
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    # Muss pd_size_multiplier(ts.score, ts.intraday_pct, ts.rvol_proxy) sein
    assert "pd_size_multiplier(ts.score, ts.intraday_pct, ts.rvol_proxy)" in src, \
        "bot.py muss alle 3 args an pd_size_multiplier passen"
