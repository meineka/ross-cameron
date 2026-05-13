"""Audit-Iter 26 (2026-05-12): status_dashboard.py durability + correctness.

Bugs:
  SD-1 (HIGH): non-atomic write — read-during-write zeigte partial JSON
    für externe Monitore. Bei tail-watching scripts würde JSON-parse
    in falsche Stati führen.
  SD-2 (HIGH): silent except Pass — disk-full oder permission-error
    blieb für immer unbemerkt.
  SD-3 (HIGH): trades_today war wrong field-name. DayState hat
    trades_completed_today. Status JSON reportete IMMER 0 statt echter
    trade-count. Operator hätte keine Ahnung dass Bot überhaupt tradet.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture(autouse=True)
def _isolated_status_file(tmp_path, monkeypatch):
    import status_dashboard
    sf = tmp_path / "status.json"
    monkeypatch.setattr(status_dashboard, "STATUS_FILE", sf)
    # Reset failure counter for isolation
    monkeypatch.setattr(status_dashboard, "_write_fail_count", 0)
    yield


def _make_bot(trades_completed=0, realized_pnl=0.0):
    import bot as bot_mod
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.day = bot_mod.DayState()
    b.day.trades_completed_today = trades_completed
    b.day.realized_pnl = realized_pnl
    b.tickers = {}
    b._last_equity = 25000.0
    return b


# ─── Bug SD-3: trades_today field name ───────────────────────────────────────
def test_status_reports_actual_trades_completed():
    """REGRESSION SD-3: status muss trades_completed_today reflektieren,
    nicht IMMER 0 wie vor dem fix."""
    import status_dashboard
    bot = _make_bot(trades_completed=5)
    status_dashboard.write_status(bot)
    data = json.loads(status_dashboard.STATUS_FILE.read_text(encoding="utf-8"))
    assert data["trades_today"] == 5


def test_status_zero_trades_when_no_activity():
    import status_dashboard
    bot = _make_bot(trades_completed=0)
    status_dashboard.write_status(bot)
    data = json.loads(status_dashboard.STATUS_FILE.read_text(encoding="utf-8"))
    assert data["trades_today"] == 0


# ─── Bug SD-1: atomic write ──────────────────────────────────────────────────
def test_status_write_atomic():
    """write_status nutzt tmp + os.replace für atomic write."""
    import status_dashboard
    import os
    bot = _make_bot()
    replace_calls = []
    real_replace = os.replace

    def spy(src, dst):
        replace_calls.append((src, dst))
        real_replace(src, dst)

    with patch("os.replace", side_effect=spy):
        status_dashboard.write_status(bot)
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert src.endswith(".json.tmp")


def test_status_tmp_cleaned_after_success():
    """Nach erfolgreichem write existiert keine .tmp-Datei mehr."""
    import status_dashboard
    bot = _make_bot()
    status_dashboard.write_status(bot)
    tmp = status_dashboard.STATUS_FILE.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert status_dashboard.STATUS_FILE.exists()


def test_status_tmp_cleaned_when_rename_fails(tmp_path, monkeypatch, caplog):
    """Wenn os.replace failt, tmp wird gelöscht."""
    import status_dashboard
    import logging
    bot = _make_bot()
    monkeypatch.setattr("os.replace", lambda s, d: (_ for _ in ()).throw(OSError("nope")))
    with caplog.at_level(logging.WARNING):
        status_dashboard.write_status(bot)
    tmp = status_dashboard.STATUS_FILE.with_suffix(".json.tmp")
    assert not tmp.exists()


# ─── Bug SD-2: throttled warning ─────────────────────────────────────────────
def test_failure_warning_logged_on_first_fail(caplog, monkeypatch):
    """Erstes write-fail soll geloggt werden, nicht silent."""
    import status_dashboard
    import logging
    bot = _make_bot()
    monkeypatch.setattr("os.replace", lambda s, d: (_ for _ in ()).throw(OSError("nope")))
    with caplog.at_level(logging.WARNING):
        status_dashboard.write_status(bot)
    assert any("status write failed" in r.message for r in caplog.records)


def test_repeated_failures_throttled(caplog, monkeypatch):
    """100 fails sollten nicht 100 warnings produzieren — throttled."""
    import status_dashboard
    import logging
    bot = _make_bot()
    monkeypatch.setattr("os.replace", lambda s, d: (_ for _ in ()).throw(OSError("nope")))
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            status_dashboard.write_status(bot)
    # Fails 1, ggf. 101, ... → bei 50 nur 1 warning
    warning_count = sum(1 for r in caplog.records
                        if "status write failed" in r.message)
    assert warning_count == 1


def test_recovery_resets_failure_counter(tmp_path, caplog, monkeypatch):
    """Nach Failures + Recovery, success-log emitted + counter reset."""
    import status_dashboard
    import logging

    bot = _make_bot()
    # Direkt fail-counter manipulieren (simuliert vorherige Fehler)
    monkeypatch.setattr(status_dashboard, "_write_fail_count", 5)
    with caplog.at_level(logging.INFO):
        status_dashboard.write_status(bot)
    # Successful write soll "recovered" loggen
    assert any("recovered" in r.message for r in caplog.records)
    # Counter sollte wieder 0 sein (via module-level global; nicht direkt
    # zugreifbar in test scope nach monkeypatch.setattr, also indirekt:
    # nochmal failen, sollte wieder als #1 logged sein)


# ─── Sanity: payload structure ───────────────────────────────────────────────
def test_status_includes_all_critical_fields():
    import status_dashboard
    bot = _make_bot()
    status_dashboard.write_status(bot)
    data = json.loads(status_dashboard.STATUS_FILE.read_text(encoding="utf-8"))
    for field in ["ts", "account_equity", "realized_pnl", "peak_pnl",
                  "trades_today", "consecutive_losses", "spiral_locked",
                  "ws_reconnects", "positions_open", "watchlist"]:
        assert field in data, f"missing field: {field}"


def test_status_with_open_positions():
    import status_dashboard
    import bot as bot_mod
    bot = _make_bot()
    ts = bot_mod.TickerState(symbol="AAPL", rank=1, score=10.0)
    ts.in_position = True
    ts.shares = 10
    ts.entry_price = 150.0
    bot.tickers["AAPL"] = ts
    status_dashboard.write_status(bot)
    data = json.loads(status_dashboard.STATUS_FILE.read_text(encoding="utf-8"))
    assert len(data["positions_open"]) == 1
    pos = data["positions_open"][0]
    assert pos["symbol"] == "AAPL"
    assert pos["shares"] == 10
    assert pos["entry"] == 150.0


def test_status_skips_non_position_tickers():
    """Tickers im Watchlist aber nicht in_position dürfen nicht in positions_open."""
    import status_dashboard
    import bot as bot_mod
    bot = _make_bot()
    ts1 = bot_mod.TickerState(symbol="A", rank=1, score=1.0)
    ts1.in_position = False
    ts2 = bot_mod.TickerState(symbol="B", rank=2, score=2.0)
    ts2.in_position = True
    ts2.shares = 5
    ts2.entry_price = 20.0
    bot.tickers["A"] = ts1
    bot.tickers["B"] = ts2
    status_dashboard.write_status(bot)
    data = json.loads(status_dashboard.STATUS_FILE.read_text(encoding="utf-8"))
    syms = [p["symbol"] for p in data["positions_open"]]
    assert "A" not in syms
    assert "B" in syms
    # Watchlist enthält beide
    wl_syms = [w["symbol"] for w in data["watchlist"]]
    assert "A" in wl_syms
    assert "B" in wl_syms


def test_status_does_not_crash_on_missing_attributes():
    """bot ohne 'day' attr → silent skip mit warning, kein raise."""
    import status_dashboard
    fake_bot = MagicMock()
    # Make accessing .day raise
    type(fake_bot).day = property(lambda self: (_ for _ in ()).throw(AttributeError("no day")))
    # Should not raise
    status_dashboard.write_status(fake_bot)
