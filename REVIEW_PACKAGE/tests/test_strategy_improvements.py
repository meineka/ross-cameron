"""Tests für die Cameron-Lessons-Verbesserungen vom 2026-05-12."""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import time as dtime

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── #1 Pump-Dump-Detection ──────────────────────────────────────────────────
def test_pump_dump_detects_extreme_score():
    from pump_dump_filter import is_pump_dump_risk, size_multiplier
    # WOK-Profil: score 4375 → unter threshold (kein PD-risk)
    assert is_pump_dump_risk(4375) is False
    # ODYS post-rescan: score 144915 → extrem
    assert is_pump_dump_risk(144915) is True
    # Borderline: 11000 → drüber
    assert is_pump_dump_risk(11000) is True
    assert is_pump_dump_risk(9999) is False


def test_pump_dump_size_multiplier_reduces():
    from pump_dump_filter import size_multiplier
    assert size_multiplier(100) == 1.0
    assert size_multiplier(50000) < 1.0
    assert size_multiplier(50000) == 0.25


def test_pump_dump_extreme_pct_rvol_combo():
    from pump_dump_filter import is_pump_dump_risk
    # Pre-Market +200% mit RVOL 100× = Pump-Dump
    assert is_pump_dump_risk(score=5000, intraday_pct=200, rvol=100) is True


# ─── #4 Open-Range-Filter ────────────────────────────────────────────────────
def test_open_range_blocks_first_5min():
    import bot
    d = bot.DayState()
    # 09:32 ET — in den ersten 5 Min
    ok, reason = bot.can_enter_new(d, dtime(9, 32))
    assert not ok
    assert reason == "open_range_5min"


def test_open_range_allows_after_935():
    import bot
    d = bot.DayState()
    d.spy_size_multiplier = 1.0  # no SPY-veto
    ok, reason = bot.can_enter_new(d, dtime(9, 36))
    assert ok is True


def test_time_new_entries_start_constant():
    import bot
    assert bot.TIME_NEW_ENTRIES_START == dtime(9, 35)


# ─── Wiring-Smoke ────────────────────────────────────────────────────────────
def test_bot_imports_pump_dump():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from pump_dump_filter import" in src
    assert "pd_size_multiplier" in src


def test_backtest_script_exists():
    p = ROOT / "06_live_bot" / "backtest_day.py"
    assert p.exists()
    src = p.read_text(encoding="utf-8")
    assert "detect_bull_flag" in src
    assert "simulate_outcome" in src
