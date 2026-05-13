"""Audit-Iter 28 (2026-05-13): day_summary_persist correctness + durability.

Bugs:
  DSP-1 (HIGH): non-atomic write — crash mid-write hätte day-summary
    halb-geschrieben gelassen. End-of-Day-Summary verloren = kompletter
    Audit-Trail für den Tag weg.
  DSP-2 (HIGH): nutzte System-Local-now-Date statt Trading-Day-Date.
    UTC-Cloud lief ggf. nach 18:00 ET noch in UTC-Datum-Wechsel rein
    → Summary in NÄCHSTEN Day's file geschrieben.
  DSP-5 (HIGH): Fehlende Felder die für Post-Mortem essentiell sind:
    trades_completed_today, adds_executed, quick_exits, goal_reached,
    spy_size_multiplier, etc.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture
def temp_results(tmp_path, monkeypatch):
    import day_summary_persist
    monkeypatch.setattr(day_summary_persist, "RESULTS_DIR", tmp_path)
    return tmp_path


def _make_day(date="2026-05-13", **kwargs):
    """Lightweight DayState-like object."""
    import bot
    d = bot.DayState()
    d.date = date
    for k, v in kwargs.items():
        setattr(d, k, v)
    return d


# ─── Bug DSP-2: Trading-Day-Date ─────────────────────────────────────────────
def test_uses_day_date_not_system_now(temp_results):
    """REGRESSION: Datei wird nach day.date benannt, nicht datetime.now()."""
    import day_summary_persist
    d = _make_day(date="2026-05-13")
    out = day_summary_persist.write_day_summary(d, spy_pct=0.5)
    assert out.name == "2026-05-13.json"


def test_falls_back_to_system_date_if_no_date(tmp_path, monkeypatch):
    """Wenn day.date leer ist → fallback auf system-now (defensive)."""
    import day_summary_persist
    import bot
    monkeypatch.setattr(day_summary_persist, "RESULTS_DIR", tmp_path)
    d = bot.DayState()
    d.date = ""  # explicitly empty
    out = day_summary_persist.write_day_summary(d)
    # Sollte nicht crashen, name ist eine YYYY-MM-DD-Form
    assert out.name.endswith(".json")
    assert len(out.stem) == 10  # YYYY-MM-DD


# ─── Bug DSP-1: Atomic write ─────────────────────────────────────────────────
def test_atomic_write_via_replace(temp_results):
    import day_summary_persist
    replace_calls = []
    real_replace = os.replace

    def spy(src, dst):
        replace_calls.append((src, dst))
        real_replace(src, dst)

    d = _make_day()
    with patch("os.replace", side_effect=spy):
        day_summary_persist.write_day_summary(d, spy_pct=0.0)
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert src.endswith(".json.tmp")
    assert dst.endswith(".json")


def test_no_tmp_left_after_success(temp_results):
    import day_summary_persist
    d = _make_day()
    out = day_summary_persist.write_day_summary(d, spy_pct=0.0)
    tmp = out.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert out.exists()


# ─── Bug DSP-5: Field completeness ───────────────────────────────────────────
def test_payload_includes_trade_outcomes(temp_results):
    """REGRESSION DSP-5: trades_completed_today + adds_executed + quick_exits"""
    import day_summary_persist
    d = _make_day(
        date="2026-05-13",
        trades_completed_today=3,
        adds_executed=2,
        quick_exits=1,
        realized_pnl=15.50,
    )
    out = day_summary_persist.write_day_summary(d, spy_pct=0.3)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["trades_completed_today"] == 3
    assert data["adds_executed"] == 2
    assert data["quick_exits"] == 1


def test_payload_includes_risk_regime(temp_results):
    """SPY-Multiplier + quarter-size + goal_reached für post-mortem."""
    import day_summary_persist
    d = _make_day(
        spy_size_multiplier=0.5,
        quarter_size_unlocked=True,
        goal_reached=True,
        cents_per_share_cumulative=0.75,
    )
    out = day_summary_persist.write_day_summary(d, spy_pct=-0.8)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["spy_size_multiplier"] == 0.5
    assert data["quarter_size_unlocked"] is True
    assert data["goal_reached"] is True
    assert data["cents_per_share_cumulative"] == 0.75


def test_payload_includes_max_trades_rejection(temp_results):
    """rejected_max_trades wird neu erfasst (alter Code hatte's nicht)."""
    import day_summary_persist
    d = _make_day(patterns_rejected_max_trades=5)
    out = day_summary_persist.write_day_summary(d)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["rejected_max_trades"] == 5


def test_payload_has_no_misleading_alpha_proxy(temp_results):
    """alpha_proxy mit hardcoded *1000 war wertlos — sollte raus sein."""
    import day_summary_persist
    d = _make_day()
    out = day_summary_persist.write_day_summary(d, spy_pct=1.5)
    data = json.loads(out.read_text(encoding="utf-8"))
    # alpha_proxy sollte NICHT mehr drin sein (oder zumindest nicht
    # mit der broken *1000-Formel)
    if "alpha_proxy" in data:
        # Falls drin: muss zumindest nicht *1000 sein
        broken_val = round(d.realized_pnl - 1.5 * 1000, 2)
        assert data["alpha_proxy"] != broken_val


# ─── Defensive ───────────────────────────────────────────────────────────────
class _MinimalDay:
    """Bare day-like object ohne die neuen Audit-Iter-28-Attrs."""
    def __init__(self, date="2026-05-13"):
        self.date = date
        self.realized_pnl = 0.0
        self.peak_pnl = 0.0
        self.bars_received = 0
        self.patterns_detected = 0
        self.patterns_rejected_macd = 0
        self.patterns_rejected_fbo = 0
        self.patterns_rejected_pullback_count = 0
        self.patterns_rejected_size_zero = 0
        self.orders_submitted = 0
        self.orders_failed = 0
        self.consecutive_losses = 0
        self.spiral_locked = False
        self.ws_reconnects = 0
        # NO: trades_completed_today, adds_executed, quick_exits etc.


def test_missing_optional_attrs_does_not_crash(temp_results):
    """Wenn DayState neue Felder fehlen (Backward-Compat), default 0/False."""
    import day_summary_persist
    out = day_summary_persist.write_day_summary(_MinimalDay())
    data = json.loads(out.read_text(encoding="utf-8"))
    # Sollte Defaults haben
    assert data["trades_completed_today"] == 0
    assert data["adds_executed"] == 0
    assert data["quick_exits"] == 0
    assert data["goal_reached"] is False
    assert data["quarter_size_unlocked"] is False
    assert data["cents_per_share_cumulative"] == 0.0
    assert data["rejected_max_trades"] == 0
    assert data["spy_size_multiplier"] == 1.0
