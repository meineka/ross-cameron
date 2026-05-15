"""Audit-Iter 30 (2026-05-13): watchlist_persist.py durability + wiring.

Bugs:
  WP-1 (HIGH): non-atomic write. Crash mid-write → corrupt JSON → loader
    returns None → bot re-scans (wasted time, 60-90s).
  WP-5: load_watchlist_if_fresh returnte nur symbols, scores wurden
    geschrieben aber konnten nicht zurückgelesen werden.
  WP-6 (CRITICAL): load_watchlist_if_fresh wurde IMPORTIERT aber NIE
    AUFGERUFEN — die ganze Mid-Day-Resume-Feature war broken seit
    Existenz. Bot re-scannte jedes Mal voll, auch bei Cloud-Restart
    innerhalb Trading-Window.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture
def temp_wlf(tmp_path, monkeypatch):
    import watchlist_persist as wp
    wf = tmp_path / "watchlist_today.json"
    monkeypatch.setattr(wp, "WATCHLIST_FILE", wf)
    yield wf


# ─── WP-1: atomic write ──────────────────────────────────────────────────────
def test_save_atomic_uses_rename(temp_wlf):
    import watchlist_persist as wp
    real_replace = os.replace
    calls = []

    def spy(s, d):
        calls.append((s, d))
        real_replace(s, d)

    with patch("os.replace", side_effect=spy):
        wp.save_watchlist(["AAPL", "TSLA"], {"AAPL": 100.0, "TSLA": 200.0})
    assert len(calls) == 1
    assert calls[0][0].endswith(".json.tmp")


def test_save_no_tmp_after_success(temp_wlf):
    import watchlist_persist as wp
    wp.save_watchlist(["A"], {"A": 1.0})
    tmp = temp_wlf.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert temp_wlf.exists()


def test_save_tmp_cleaned_on_rename_failure(temp_wlf, monkeypatch, caplog):
    import watchlist_persist as wp
    import logging
    monkeypatch.setattr("os.replace",
                        lambda s, d: (_ for _ in ()).throw(OSError("disk full")))
    with caplog.at_level(logging.WARNING):
        wp.save_watchlist(["A"], {"A": 1.0})
    tmp = temp_wlf.with_suffix(".json.tmp")
    assert not tmp.exists()


# ─── WP-5: load_watchlist_with_scores ────────────────────────────────────────
def test_load_with_scores_returns_both(temp_wlf):
    import watchlist_persist as wp
    wp.save_watchlist(["A", "B"], {"A": 100.0, "B": 50.0})
    result = wp.load_watchlist_with_scores()
    assert result is not None
    syms, scores = result
    assert syms == ["A", "B"]
    assert scores == {"A": 100.0, "B": 50.0}


def test_load_with_scores_handles_missing_scores(temp_wlf):
    """Wenn scores fehlen → leeres dict, kein crash."""
    import watchlist_persist as wp
    # P2.6 fix (Phase-63): the bot's "today" is NY trading-day, not
    # local-time. After Berlin midnight but before NY midnight (the
    # 6-hour window 00:00-06:00 CET), datetime.now().strftime would
    # produce tomorrow-NY which the loader rejects as stale. Use the
    # same helper the bot uses.
    today = wp._ny_today_str()
    temp_wlf.write_text(json.dumps({
        "date": today, "symbols": ["X"],
        # no 'scores' field
    }), encoding="utf-8")
    result = wp.load_watchlist_with_scores()
    assert result is not None
    syms, scores = result
    assert syms == ["X"]
    assert scores == {}


def test_load_with_scores_returns_none_when_stale(temp_wlf):
    """Yesterday's watchlist → None. Phase-63 fix: use NY trading-day
    helper, not local time (see sibling test for rationale)."""
    import watchlist_persist as wp
    today_ny = datetime.strptime(wp._ny_today_str(), "%Y-%m-%d")
    yesterday = (today_ny - timedelta(days=1)).strftime("%Y-%m-%d")
    temp_wlf.write_text(json.dumps({
        "date": yesterday, "symbols": ["OLD"],
    }), encoding="utf-8")
    assert wp.load_watchlist_with_scores() is None


def test_load_with_scores_returns_none_when_corrupt(temp_wlf, caplog):
    import watchlist_persist as wp
    import logging
    temp_wlf.write_text("garbage{not json}", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = wp.load_watchlist_with_scores()
    assert result is None
    assert any("corrupt" in r.message.lower() for r in caplog.records)


def test_load_with_scores_returns_none_when_no_file():
    import watchlist_persist as wp
    with tempfile.TemporaryDirectory() as tmp:
        bogus = Path(tmp) / "nonexistent.json"
        with patch.object(wp, "WATCHLIST_FILE", bogus):
            assert wp.load_watchlist_with_scores() is None


def test_load_with_scores_handles_non_dict_payload(temp_wlf, caplog):
    """Wenn JSON eine List ist (alte/falsche Version) → None + warning."""
    import watchlist_persist as wp
    import logging
    temp_wlf.write_text('["AAPL", "TSLA"]', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = wp.load_watchlist_with_scores()
    assert result is None


# ─── Legacy API: load_watchlist_if_fresh (symbols only) ─────────────────────
def test_legacy_load_if_fresh_returns_symbols(temp_wlf):
    import watchlist_persist as wp
    wp.save_watchlist(["A", "B"], {})
    syms = wp.load_watchlist_if_fresh()
    assert syms == ["A", "B"]


def test_legacy_load_if_fresh_returns_none_stale(temp_wlf):
    """Phase-63 fix: same NY-trading-day fix as the sibling tests."""
    import watchlist_persist as wp
    today_ny = datetime.strptime(wp._ny_today_str(), "%Y-%m-%d")
    yesterday = (today_ny - timedelta(days=1)).strftime("%Y-%m-%d")
    temp_wlf.write_text(json.dumps({
        "date": yesterday, "symbols": ["OLD"], "scores": {},
    }), encoding="utf-8")
    assert wp.load_watchlist_if_fresh() is None


# ─── WP-6: bot.py wires load_watchlist_with_scores ──────────────────────────
def test_bot_imports_load_watchlist_with_scores():
    """REGRESSION WP-6: bot.py muss load_watchlist_with_scores importieren
    UND aufrufen, sonst war ganzes Feature broken."""
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert "load_watchlist_with_scores" in src, \
        "bot.py muss load_watchlist_with_scores importieren"
    # Must actually CALL it, not just import
    assert "load_watchlist_with_scores()" in src, \
        "bot.py muss load_watchlist_with_scores() aufrufen — sonst broken"


def test_bot_mid_day_resume_logs_disk_load():
    """bot.py muss explicit MID-DAY-RESUME loggen wenn disk load."""
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert "MID-DAY-RESUME" in src
    # Sanity: log message format expected by audit script
    assert "Watchlist aus Disk geladen" in src or "MID-DAY-RESUME" in src


# ─── clear_watchlist defensive ───────────────────────────────────────────────
def test_clear_watchlist_no_crash_when_missing(temp_wlf):
    import watchlist_persist as wp
    # File doesn't exist yet
    wp.clear_watchlist()  # should not raise


def test_clear_watchlist_removes_file(temp_wlf):
    import watchlist_persist as wp
    wp.save_watchlist(["A"], {})
    assert temp_wlf.exists()
    wp.clear_watchlist()
    assert not temp_wlf.exists()
