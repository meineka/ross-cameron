"""WS-Loop-Bugs aus Audit-Iteration 3."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug D: ws_reconnects-Counter ────────────────────────────────────────────
def test_ws_reconnects_counts_both_clean_and_error():
    """ws_reconnects-Counter muss BEIDE Pfade abdecken: clean disconnect + Exception.
    Source-Check als Smoke: beide Pfade incrementieren."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Beide ws_reconnects += 1 müssen vorhanden sein:
    # 1× im try-Block (clean) und 1× im except-Block (error)
    assert src.count("self.day.ws_reconnects += 1") >= 2, \
        "ws_reconnects sollte sowohl bei clean disconnect als auch bei Exception incrementieren"


# ─── Bug E: backoff NUR nach echtem Fehler ───────────────────────────────────
def test_ws_loop_uses_had_error_flag():
    """Audit-Iteration 3: backoff nur in error-path triggern, sonst kurzer sleep."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Beide Patterns müssen da sein:
    assert "had_error = True" in src
    assert "if had_error:" in src
    # Saubere Disconnects haben kurzen sleep, nicht backoff
    # (zumindest sollte backoff.sleep_after_fail nicht UNBEDINGT immer aufgerufen werden)


# ─── Behavior-Smoke: ReconnectBackoff funktioniert noch ──────────────────────
def test_backoff_full_recovery_cycle():
    """Vollständiger Zyklus: fail x5, reset, fail wieder = funktional ok."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=10.0, max_consec_fails=10)
    delays = [b.fail() for _ in range(5)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0]  # exponential then capped
    b.reset()
    assert b.consec_fails == 0
    assert b.fail() == 1.0  # zurück bei base


def test_backoff_no_crash_on_reset_without_prior_fail():
    """Reset auf frischen Backoff darf nicht crashen."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff()
    b.reset()
    b.reset()
    assert b.consec_fails == 0
