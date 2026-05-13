"""Audit-Iter 6 (2026-05-12): position_recovery.recover_or_flatten robustness.

Vorher (Bug PR-1, PR-2, PR-6):
  - Single-shot close_all_positions ohne retry
  - Return-Value = len(positions) auch bei Failure → caller dachte "ok"
  - Keine Fill-Verification

Jetzt:
  - 3 Attempts mit Polling
  - Return-Codes:
       0 = clean, keine Positionen
      -1 = recovery FAILED (caller MUSS bot stoppen)
      >0 = N Positionen erfolgreich geflattened
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _pos(symbol: str, qty: int = 10):
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.avg_entry_price = "5.00"
    return p


# ─── Return-Code-Semantik (PR-2) ─────────────────────────────────────────────
def test_returns_zero_when_already_flat():
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = []
    assert recover_or_flatten(tc) == 0


def test_returns_minus_one_when_get_positions_raises():
    """API-Down beim Initial-Check → -1, NICHT len(positions)=0."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.side_effect = RuntimeError("API down")
    assert recover_or_flatten(tc) == -1


def test_returns_minus_one_when_flatten_fails_persistently():
    """Positionen bleiben offen nach 3 Attempts → -1 (caller muss abort)."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = [_pos("STUCK")]
    rc = recover_or_flatten(tc, max_attempts=2, verify_timeout_sec=0.1,
                             poll_interval_sec=0.05)
    assert rc == -1


def test_returns_count_when_eventually_flat():
    """Position fades after retry → return initial count, NOT -1."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    state = {"closes": 0}

    def close_side(*a, **kw):
        state["closes"] += 1

    def list_side(*a, **kw):
        # Erste Attempts: position bleibt; nach 2 Closes: leer
        if state["closes"] >= 2:
            return []
        return [_pos("X"), _pos("Y")]

    tc.get_all_positions.side_effect = list_side
    tc.close_all_positions.side_effect = close_side
    rc = recover_or_flatten(tc, max_attempts=3, verify_timeout_sec=0.1,
                             poll_interval_sec=0.05)
    assert rc == 2  # initial count


# ─── Retry-Verhalten (PR-1) ──────────────────────────────────────────────────
def test_retries_close_all_on_exception():
    """API raised → next attempt versucht es nochmal."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    state = {"closes": 0}

    def close_side(*a, **kw):
        state["closes"] += 1
        if state["closes"] == 1:
            raise RuntimeError("API hiccup")

    def list_side(*a, **kw):
        return [] if state["closes"] >= 2 else [_pos("AAA")]

    tc.get_all_positions.side_effect = list_side
    tc.close_all_positions.side_effect = close_side
    rc = recover_or_flatten(tc, max_attempts=3, verify_timeout_sec=0.1,
                             poll_interval_sec=0.05)
    assert rc == 1
    assert state["closes"] == 2  # 1x failed + 1x success


# ─── Polling-Verhalten (PR-6) ────────────────────────────────────────────────
def test_polls_until_positions_empty():
    """close_all submitted → fills sind async → poll wartet bis 0."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    state = {"polls": 0}

    def list_side(*a, **kw):
        state["polls"] += 1
        # Initial-Liste: 1 Pos. Poll-Round 2: still. Poll 3: empty.
        if state["polls"] >= 3:
            return []
        return [_pos("SLOW_FILL")]

    tc.get_all_positions.side_effect = list_side
    rc = recover_or_flatten(tc, max_attempts=2, verify_timeout_sec=1.0,
                             poll_interval_sec=0.05)
    assert rc == 1
    assert state["polls"] >= 3


# ─── report-only mode ────────────────────────────────────────────────────────
def test_report_only_does_not_close():
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = [_pos("AAA"), _pos("BBB")]
    rc = recover_or_flatten(tc, mode="report-only")
    assert rc == 2
    tc.close_all_positions.assert_not_called()


def test_unknown_mode_returns_minus_one():
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = [_pos("AAA")]
    assert recover_or_flatten(tc, mode="garbage") == -1


# ─── Audit-Trail ─────────────────────────────────────────────────────────────
def test_logs_each_symbol_before_close(caplog):
    """Vor close_all sollen alle Symbole geloggt sein für Audit-Trail."""
    import logging
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    state = {"closes": 0}

    def list_side(*a, **kw):
        return [] if state["closes"] >= 1 else [_pos("AUDIT_ME")]

    def close_side(*a, **kw):
        state["closes"] += 1

    tc.get_all_positions.side_effect = list_side
    tc.close_all_positions.side_effect = close_side
    with caplog.at_level(logging.WARNING, logger="recovery"):
        recover_or_flatten(tc, verify_timeout_sec=0.1, poll_interval_sec=0.05)
    assert any("AUDIT_ME" in r.message for r in caplog.records)
