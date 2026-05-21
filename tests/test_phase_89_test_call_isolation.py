"""Phase-89 (2026-05-21): isolate test-source calls from production logs + ntfy.

ROOT CAUSE OF "ALPACA RATE-LIMITED 250/min" SPAM:
  test_phase_53_guarded_alpaca::test_guarded_invoke_fail_closed_when_budget_exhausted
  fires 205 calls with source="test" into `guarded_alpaca._guarded_invoke`.
  Those calls were writing to the production `alpaca_api_calls.jsonl`
  AND triggering `_maybe_push_state_transition` which pushed ntfy to
  the operator's phone.

  Result: 4-5 false-positive "ALPACA RATE-LIMITED" ntfy pushes per CI
  smoke-test run = every workflow_dispatch produced spurious alerts.

Fix:
  1. `_log_call` routes source="test" entries to alpaca_api_calls_test.jsonl
     (separate file, never read by production rate-limit analysis).
  2. `_maybe_push_state_transition` early-returns when source starts
     with "test" — no ntfy push.

Tests forbidden patterns + pin behavior forever.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _src() -> str:
    return (ROOT / "06_live_bot" / "guarded_alpaca.py").read_text(
        encoding="utf-8"
    )


def test_log_call_routes_test_source_to_separate_file():
    """source='test' must NOT write to production alpaca_api_calls.jsonl."""
    src = _src()
    assert "alpaca_api_calls_test.jsonl" in src
    # And the routing condition is source.startswith("test")
    import re
    block = re.search(
        r'source\.startswith\("test"\)[\s\S]{0,200}alpaca_api_calls_test\.jsonl',
        src,
    )
    assert block, "test-source must be routed to alpaca_api_calls_test.jsonl"


def test_state_transition_push_skips_test_source():
    """_maybe_push_state_transition must early-return on test source."""
    src = _src()
    import re
    # Look for an early-return when source starts with "test" — allow
    # docstrings between def and the actual check
    block = re.search(
        r"def _maybe_push_state_transition[\s\S]{0,1500}?"
        r'source\.startswith\(["\']test["\']\)[\s\S]{0,200}?return',
        src,
    )
    assert block, (
        "_maybe_push_state_transition must early-return for "
        "source.startswith('test')"
    )


def test_test_calls_actually_go_to_test_file():
    """Live integration: fire a test-sourced call and verify it lands
    in the test log file, NOT the production log."""
    import importlib
    import guarded_alpaca
    importlib.reload(guarded_alpaca)
    from guarded_alpaca import _guarded_invoke
    from alpaca_rate_guard import RateGuard

    test_log_size_before = 0
    test_file = guarded_alpaca.HERE / "alpaca_api_calls_test.jsonl"
    if test_file.exists():
        test_log_size_before = test_file.stat().st_size

    prod_log_size_before = 0
    if guarded_alpaca.ALPACA_API_CALLS_LOG.exists():
        prod_log_size_before = guarded_alpaca.ALPACA_API_CALLS_LOG.stat().st_size

    guard = RateGuard()
    _guarded_invoke(
        guard=guard, source="test", method_name="call_phase89_unit",
        callable_fn=lambda: "ok", args=(), kwargs={},
        block_timeout_sec=1.0,
    )

    # Production log must NOT have grown
    prod_log_size_after = (
        guarded_alpaca.ALPACA_API_CALLS_LOG.stat().st_size
        if guarded_alpaca.ALPACA_API_CALLS_LOG.exists() else 0
    )
    assert prod_log_size_after == prod_log_size_before, (
        f"production log grew by {prod_log_size_after - prod_log_size_before} "
        f"bytes — test-source LEAKED into production log"
    )

    # Test log MUST have grown (if writable)
    if test_file.exists():
        test_log_size_after = test_file.stat().st_size
        assert test_log_size_after > test_log_size_before, (
            "test log did not grow — _log_call routing broken"
        )


def test_phase_89_comment_present():
    src = _src()
    assert "Phase-89" in src
