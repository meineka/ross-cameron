"""Phase-61 (re-audit follow-up to Phase-60): state-transition push tests
for the Alpaca rate-limit guard.

Re-audit found that `_rate_limit_state_changed_ts` was set but never read
(dead code) and that the state-transition push mechanism had ZERO test
coverage. This file fills both gaps:

  1. Exactly ONE push fires on ok → blocked transition (no spam during
     sustained block).
  2. Exactly ONE push fires on blocked → ok transition (recovery).
  3. Debounce: ok → blocked → ok within 60s suppresses the third push
     (flap protection).
  4. The dead timestamp variable is now functional — verified by
     simulating a flap and asserting suppression vs allow.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


class _FakeAlerter:
    """Captures (level, title, body) tuples sent via .send()."""

    def __init__(self):
        self.pushes: list[tuple[str, str, str]] = []

    def send(self, level, title, body="", *, force=False):
        self.pushes.append((level, title, body))
        return True


@pytest.fixture
def reset_guarded_state(monkeypatch, tmp_path):
    """Reset module-global state in guarded_alpaca to a clean slate AND
    point the JSONL log at tmp so we don't pollute the real file."""
    import guarded_alpaca
    monkeypatch.setattr(guarded_alpaca, "_rate_limit_state", "ok")
    monkeypatch.setattr(guarded_alpaca, "_rate_limit_state_changed_ts", 0.0)
    monkeypatch.setattr(guarded_alpaca, "ALPACA_API_CALLS_LOG",
                          tmp_path / "alpaca_api_calls.jsonl")
    yield guarded_alpaca


@pytest.fixture
def fake_alerter(monkeypatch):
    """Patch alerter.make_alerter to return our FakeAlerter."""
    fa = _FakeAlerter()
    import alerter
    monkeypatch.setattr(alerter, "make_alerter", lambda: fa)
    return fa


def _hammer_blocked(guarded_alpaca, n: int):
    """Fire `n` calls all of which are denied by the guard."""
    guard = MagicMock()
    guard.block_until_allowed.return_value = False
    guard.max_per_min = 200
    guard.current_rate_per_min = 250
    for i in range(n):
        try:
            guarded_alpaca._guarded_invoke(
                guard=guard, source="alpaca-trading",
                method_name=f"call_{i}", callable_fn=lambda: "X",
                args=(), kwargs={}, block_timeout_sec=0.001,
            )
        except guarded_alpaca.AlpacaRateLimitBlocked:
            pass


def _hammer_ok(guarded_alpaca, n: int):
    """Fire `n` calls all of which the guard allows."""
    guard = MagicMock()
    guard.block_until_allowed.return_value = True
    guard.max_per_min = 200
    guard.current_rate_per_min = 50
    for i in range(n):
        guarded_alpaca._guarded_invoke(
            guard=guard, source="alpaca-trading",
            method_name=f"call_{i}", callable_fn=lambda: "X",
            args=(), kwargs={}, block_timeout_sec=0.001,
        )


# ─── 1. Exactly ONE blocked push per transition ─────────────────────────

def test_only_one_blocked_push_per_transition(reset_guarded_state,
                                                 fake_alerter):
    """Fire 50 calls all of which are denied. Push should fire ONCE
    (on first transition ok→blocked), not 50 times."""
    _hammer_blocked(reset_guarded_state, 50)
    blocked_pushes = [p for p in fake_alerter.pushes
                       if "RATE-LIMITED" in p[1]]
    assert len(blocked_pushes) == 1, (
        f"expected 1 blocked-push, got {len(blocked_pushes)}: "
        f"{[p[1] for p in fake_alerter.pushes]}"
    )
    # State is now "blocked"
    assert reset_guarded_state._rate_limit_state == "blocked"


# ─── 2. Recovery push fires on blocked→ok ────────────────────────────────

def test_recovery_push_fires_on_blocked_to_ok(reset_guarded_state,
                                                 fake_alerter):
    """First fire 10 blocked, then 10 allowed. Recovery push must fire
    exactly once on the first allow after the block."""
    _hammer_blocked(reset_guarded_state, 10)
    assert reset_guarded_state._rate_limit_state == "blocked"
    # Now sleep past the debounce window so recovery isn't suppressed
    import guarded_alpaca
    guarded_alpaca._rate_limit_state_changed_ts = 0.0  # bypass debounce
    _hammer_ok(reset_guarded_state, 10)
    recovery_pushes = [p for p in fake_alerter.pushes
                        if "RECOVERED" in p[1]]
    assert len(recovery_pushes) == 1, (
        f"expected 1 recovery-push, got {len(recovery_pushes)}: "
        f"{[p[1] for p in fake_alerter.pushes]}"
    )
    assert reset_guarded_state._rate_limit_state == "ok"


# ─── 3. Debounce: flap ok→blocked→ok within window is suppressed ────────

def test_flap_within_debounce_window_suppresses_third_push(
        reset_guarded_state, fake_alerter):
    """Phase-61 fix: ts variable now ACTIVE. A rapid flap
    ok→blocked→ok should produce ONE blocked-push, then the recovery
    is suppressed because it lands within STATE_TRANSITION_DEBOUNCE_SEC.
    State still updates internally — only the push is suppressed."""
    import guarded_alpaca
    # First transition fires normally (changed_ts==0 starts the clock)
    _hammer_blocked(reset_guarded_state, 5)
    assert guarded_alpaca._rate_limit_state == "blocked"
    pushes_after_block = len(fake_alerter.pushes)
    assert pushes_after_block == 1
    # Now flap back to "ok" IMMEDIATELY (changed_ts is recent → debounce)
    _hammer_ok(reset_guarded_state, 5)
    # State updated, but no NEW push because we're inside debounce window
    assert guarded_alpaca._rate_limit_state == "ok"
    assert len(fake_alerter.pushes) == pushes_after_block, (
        f"debounce broken: expected no new push within window, got "
        f"{[p[1] for p in fake_alerter.pushes[pushes_after_block:]]}"
    )


# ─── 4. Past debounce window allows new push ────────────────────────────

def test_transition_after_debounce_window_fires_push(reset_guarded_state,
                                                        fake_alerter):
    """Symmetry check to #3: if the prior transition is OLDER than
    STATE_TRANSITION_DEBOUNCE_SEC, the new push is allowed through."""
    import guarded_alpaca
    _hammer_blocked(reset_guarded_state, 5)
    assert len(fake_alerter.pushes) == 1
    # Simulate time-travel past debounce window
    guarded_alpaca._rate_limit_state_changed_ts -= (
        guarded_alpaca.STATE_TRANSITION_DEBOUNCE_SEC + 5
    )
    _hammer_ok(reset_guarded_state, 5)
    assert len(fake_alerter.pushes) == 2
    recovery = fake_alerter.pushes[-1]
    assert "RECOVERED" in recovery[1]


# ─── 5. No-op: state stable → no push ───────────────────────────────────

def test_no_push_when_state_unchanged(reset_guarded_state, fake_alerter):
    """Already-ok state + ok calls = no push."""
    _hammer_ok(reset_guarded_state, 20)
    assert len(fake_alerter.pushes) == 0
    assert reset_guarded_state._rate_limit_state == "ok"


# ─── 6. Debounce constant has sane value ────────────────────────────────

def test_debounce_constant_is_sane():
    """Spec: STATE_TRANSITION_DEBOUNCE_SEC must be >=30s (less and
    flaps still get through) and <=300s (more and operator never gets
    realtime feedback during a real outage)."""
    import guarded_alpaca
    assert 30.0 <= guarded_alpaca.STATE_TRANSITION_DEBOUNCE_SEC <= 300.0
