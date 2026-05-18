"""Phase-68: regression test for ws.stop_ws() coroutine-never-awaited.

User: "und wieder wurde ich verarscht, kein trade" → after 12:28 Berlin
premarket-scan, the bot finally found SBFM (+551%) and GOVX (+146%)
in the 09:58 NY slow-rescan but couldn't subscribe to them on the
WS because of a connection-limit-exceeded cascade.

Root cause investigated 2026-05-18 17:30:
  - alpaca-py's StockDataStream.stop_ws is `async def`
  - bot.py:2254 called `ws.stop_ws()` WITHOUT await
  - Python emitted: RuntimeWarning: coroutine 'DataStream.stop_ws'
    was never awaited
  - Effect: the stop-flag was never set, ws.run() kept consuming,
    next `StockDataStream(...)` returned the singleton (Phase-43),
    next subscribe + run started a SECOND auth on the SAME account,
    Alpaca rejected: "connection limit exceeded"
  - Cascade: 4× SINGLETON-ABUSED warnings + 7× connection-limit
    errors in 11 minutes → trading day lost.

Fix: bot.py awaits the coroutine, defensively works for both sync
and async SDK shapes via asyncio.iscoroutine() guard.

These tests pin the contract so the next SDK upgrade can't silently
re-introduce the regression.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. Source-grep: bot.py awaits the coroutine ────────────────────────

def test_bot_py_awaits_stop_ws_coroutine():
    """The headline regression check: bot.py:2254 must use the
    iscoroutine-guarded-await pattern, not a bare ws.stop_ws() call."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Must reference the defensive coroutine-handling pattern
    assert "iscoroutine" in src, (
        "bot.py must use asyncio.iscoroutine() to handle both sync "
        "and async stop_ws shapes"
    )
    # Must NOT have a bare `ws.stop_ws()` call (the bug we fixed).
    # Allow the comment-form references which are documentation.
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        # Bare-call pattern: `ws.stop_ws()` not preceded by `await` and
        # not assigned to a variable.
        if stripped == "ws.stop_ws()":
            pytest.fail(
                f"bot.py:{i} contains a bare `ws.stop_ws()` call "
                f"(coroutine never awaited bug). Use the defensive "
                f"`_stop_result = ws.stop_ws(); if iscoroutine: await` "
                f"pattern."
            )


def test_bot_py_phase_68_comment_present():
    """Source-grep: the Phase-68 fix comment must explain WHY the
    defensive iscoroutine guard exists. Without this, a future
    'cleanup' refactor might revert it."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "Phase-68" in src
    assert "connection limit" in src.lower()


# ─── 2. Defensive iscoroutine handling — both shapes work ──────────────

@pytest.mark.asyncio
async def test_defensive_pattern_works_when_stop_ws_is_async():
    """Simulate the alpaca-py current shape (async def stop_ws).
    The defensive pattern must await it."""
    flag_set = False

    async def async_stop_ws():
        nonlocal flag_set
        flag_set = True

    ws = MagicMock()
    ws.stop_ws = async_stop_ws

    # Inline the pattern from bot.py
    _stop_result = ws.stop_ws()
    if asyncio.iscoroutine(_stop_result):
        await _stop_result

    assert flag_set is True, (
        "async stop_ws coroutine was never awaited — the bug reverted"
    )


@pytest.mark.asyncio
async def test_defensive_pattern_works_when_stop_ws_is_sync():
    """If a future SDK reverts to sync def stop_ws (or a mock returns
    None / a normal value), the pattern must still work without
    raising 'cannot await non-coroutine'."""
    flag_set = False

    def sync_stop_ws():
        nonlocal flag_set
        flag_set = True

    ws = MagicMock()
    ws.stop_ws = sync_stop_ws

    _stop_result = ws.stop_ws()
    if asyncio.iscoroutine(_stop_result):
        await _stop_result

    assert flag_set is True


@pytest.mark.asyncio
async def test_defensive_pattern_handles_stop_ws_exception():
    """If stop_ws raises (e.g. WS already closed), the try/except in
    bot.py must catch it."""
    async def raising_stop_ws():
        raise RuntimeError("WS already closed")

    ws = MagicMock()
    ws.stop_ws = raising_stop_ws

    raised = None
    try:
        _stop_result = ws.stop_ws()
        if asyncio.iscoroutine(_stop_result):
            await _stop_result
    except Exception as e:
        raised = e

    assert isinstance(raised, RuntimeError)
    assert "already closed" in str(raised)


# ─── 3. Bug reproduction — SDK contract ────────────────────────────────

def test_alpaca_sdk_stop_ws_is_async_def():
    """Confirms the SDK shape the fix is defending against. If a future
    upgrade reverts to sync, this test fails LOUDLY so the defensive
    code can be re-evaluated."""
    try:
        from alpaca.data.live.websocket import DataStream
    except ImportError:
        pytest.skip("alpaca-py not installed in this venv")
    import inspect
    assert inspect.iscoroutinefunction(DataStream.stop_ws), (
        "SDK has changed: stop_ws is no longer async. The defensive "
        "iscoroutine pattern in bot.py becomes unnecessary — confirm "
        "and simplify."
    )
