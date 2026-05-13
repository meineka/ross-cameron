"""Audit-Iter 17 (2026-05-12): TradeLogger + slippage_log robustness.

Bugs gefunden:
  LOG-1 (HIGH): kein flush/fsync → crash zwischen buffer und disk
    verlor recent trade-events. Cloud-Restart hat das oft.
  LOG-2 (MED): kein Lock → async on_bar handlers konnten parallel
    schreiben → corrupt JSONL mit interleaved lines.
  LOG-3 (MED): kein try/except → disk-full/permission crashed bot mid-trade.
  LOG-5 (MED): slippage_log silent 0.0 drift_pct wenn expected<=0
    → Post-Mortem konnte data-error nicht erkennen.
"""
from __future__ import annotations
import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── TradeLogger ─────────────────────────────────────────────────────────────
def test_trade_logger_writes_jsonl_event(tmp_path, monkeypatch):
    """Sanity: schreibt JSON-Line mit ts."""
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    tl.log({"event": "entry", "symbol": "AAPL", "shares": 10})
    content = tl.path.read_text(encoding="utf-8").strip()
    assert content
    parsed = json.loads(content)
    assert parsed["event"] == "entry"
    assert parsed["symbol"] == "AAPL"
    assert "ts" in parsed


def test_trade_logger_appends_multiple(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    tl.log({"event": "a"})
    tl.log({"event": "b"})
    lines = tl.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_trade_logger_does_not_crash_on_unserializable(tmp_path, monkeypatch, caplog):
    """LOG-3: bot darf nicht crashen wenn event komische types enthält."""
    import bot
    import logging
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    with caplog.at_level(logging.WARNING):
        tl.log({"event": "broken", "obj": object()})  # object() nicht json-bar
    # No file written, warning logged, bot lebt
    assert not tl.path.exists() or tl.path.stat().st_size == 0


def test_trade_logger_does_not_crash_on_permission_error(tmp_path, monkeypatch, caplog):
    """LOG-3: write-fail soll nur warning sein, kein crash."""
    import bot
    import logging
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    # Simuliere Permission-Error indem wir path zu read-only ordner machen
    bad_path = tmp_path / "nonexistent_dir" / "file.jsonl"
    tl.path = bad_path
    with caplog.at_level(logging.WARNING):
        tl.log({"event": "x"})
    # Sollte nicht raisen
    assert "failed" in " ".join(r.message for r in caplog.records).lower()


def test_trade_logger_lock_serializes_concurrent_writes(tmp_path, monkeypatch):
    """LOG-2: 2 Threads schreiben gleichzeitig → keine corrupt-lines."""
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    results = []

    def writer(n):
        for i in range(20):
            tl.log({"event": f"t{n}", "i": i})

    threads = [threading.Thread(target=writer, args=(j,)) for j in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Jede Zeile muss valid JSON sein (kein interleaving)
    lines = tl.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 80  # 4 threads * 20
    for line in lines:
        json.loads(line)  # would raise if corrupted


def test_trade_logger_flushes_immediately(tmp_path, monkeypatch):
    """LOG-1: nach log() muss event auf disk sichtbar sein (kein Buffer-Hold)."""
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    tl.log({"event": "flush-test"})
    # Direkt read ohne dass Process exit → flush funktionierte
    assert tl.path.exists()
    assert tl.path.stat().st_size > 0


# ─── slippage_log ────────────────────────────────────────────────────────────
def test_record_fill_drift_known_when_expected_positive(tmp_path, monkeypatch):
    import slippage_log
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    entry = slippage_log.record_fill("AAPL", "buy", 10, 100.0, 100.5)
    assert entry["drift_known"] is True
    assert abs(entry["drift_pct"] - 0.5) < 0.01


def test_record_fill_drift_unknown_when_expected_zero(tmp_path, monkeypatch):
    """LOG-5: expected=0 → drift_known=False, NICHT silent 0.0."""
    import slippage_log
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    entry = slippage_log.record_fill("AAPL", "buy", 10, 0.0, 5.0)
    assert entry["drift_known"] is False
    assert entry["drift_pct"] == 0.0


def test_record_fill_drift_unknown_when_expected_negative(tmp_path, monkeypatch):
    import slippage_log
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    entry = slippage_log.record_fill("AAPL", "buy", 10, -1.0, 5.0)
    assert entry["drift_known"] is False


def test_record_fill_does_not_crash_on_permission_error(tmp_path, monkeypatch, caplog):
    """LOG-3: write-fail = warning, kein crash."""
    import slippage_log
    import logging
    bad_path = tmp_path / "no_such_dir" / "s.jsonl"
    monkeypatch.setattr(slippage_log, "SLIP_FILE", bad_path)
    with caplog.at_level(logging.WARNING):
        e = slippage_log.record_fill("X", "buy", 1, 10.0, 10.1)
    # Returns entry trotz Failure
    assert e["symbol"] == "X"


def test_record_fill_alerts_on_high_drift(tmp_path, monkeypatch, caplog):
    """Drift > 0.5% → ALERT log."""
    import slippage_log
    import logging
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    with caplog.at_level(logging.WARNING):
        slippage_log.record_fill("AAPL", "buy", 10, 100.0, 101.0)  # 1% drift
    assert any("SLIPPAGE-ALERT" in r.message for r in caplog.records)


def test_record_fill_alerts_data_err_on_zero_expected(tmp_path, monkeypatch, caplog):
    """LOG-5: explicit warning bei data-error (expected<=0)."""
    import slippage_log
    import logging
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    with caplog.at_level(logging.WARNING):
        slippage_log.record_fill("X", "buy", 1, 0.0, 5.0)
    assert any("SLIPPAGE-DATA-ERR" in r.message for r in caplog.records)


def test_record_fill_no_alert_on_small_drift(tmp_path, monkeypatch, caplog):
    """Drift < 0.5% → kein ALERT."""
    import slippage_log
    import logging
    monkeypatch.setattr(slippage_log, "SLIP_FILE", tmp_path / "s.jsonl")
    with caplog.at_level(logging.WARNING):
        slippage_log.record_fill("AAPL", "buy", 10, 100.0, 100.1)  # 0.1%
    assert not any("ALERT" in r.message for r in caplog.records)
