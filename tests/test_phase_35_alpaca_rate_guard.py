"""Phase-35: Alpaca rate-guard + 5-sec stall-probe.

User-requested 2026-05-15: "max alpaca calls 200 per min, wenn stalled,
testcall alle 5 Sekunden. Nimm das in die Logik auf."

Tests cover:
  - Rate-guard constants are exposed at the documented values
  - bot.py re-exports them for operator visibility
  - RateGuard blocks the (max+1)th call within 60s
  - RateGuard's current_rate_per_min reflects actual call density
  - probe_ws_slot_free returns (True, ...) on a successful auth
  - probe_ws_slot_free returns (False, ...) on connection limit
  - wait_until_ws_slot_free polls every 5s until free OR timeout
  - alpaca_ws_patch invokes wait_until_ws_slot_free on conn-limit error
"""
from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_constants_at_documented_values():
    from alpaca_rate_guard import (
        ALPACA_MAX_CALLS_PER_MIN,
        ALPACA_STALL_PROBE_INTERVAL_SEC,
        ALPACA_STALL_AFTER_N_FAILS,
    )
    assert ALPACA_MAX_CALLS_PER_MIN == 200
    assert ALPACA_STALL_PROBE_INTERVAL_SEC == 5
    assert ALPACA_STALL_AFTER_N_FAILS == 1


def test_bot_reexports_rate_limit_constants():
    """bot.py must expose the rate-limit constants for operator visibility."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from alpaca_rate_guard import" in src
    assert "ALPACA_MAX_CALLS_PER_MIN" in src
    assert "ALPACA_STALL_PROBE_INTERVAL_SEC" in src


def test_rate_guard_passes_under_budget():
    from alpaca_rate_guard import RateGuard
    g = RateGuard(max_per_min=10)
    # 5 calls — well under budget — all pass instantly
    t0 = time.monotonic()
    for _ in range(5):
        ok, _ = g.can_proceed()
        assert ok
        g.consume()
    assert time.monotonic() - t0 < 0.5
    assert g.current_rate_per_min == 5


def test_rate_guard_blocks_over_budget():
    """The 11th call within 60s of 10 prior calls is denied."""
    from alpaca_rate_guard import RateGuard
    g = RateGuard(max_per_min=10)
    for _ in range(10):
        g.consume()
    ok, sleep_for = g.can_proceed()
    assert ok is False
    assert sleep_for > 0


def test_rate_guard_block_until_allowed_timeout():
    """block_until_allowed must return False when budget exhausted
    and timeout elapses, NOT crash or block indefinitely."""
    from alpaca_rate_guard import RateGuard
    g = RateGuard(max_per_min=2)
    g.consume()
    g.consume()
    # 3rd call would have to wait ~60s — request a 1s timeout
    t0 = time.monotonic()
    ok = g.block_until_allowed(timeout_sec=1.0)
    assert ok is False
    assert time.monotonic() - t0 < 3.0
    assert g.block_count == 1


def test_rate_guard_context_manager_consumes_token():
    from alpaca_rate_guard import RateGuard
    g = RateGuard(max_per_min=5)
    with g:
        pass  # 1 call
    with g:
        pass  # 2 calls
    assert g.current_rate_per_min == 2


def test_rate_guard_aged_out_tokens_release_capacity():
    """After 60s, the oldest tokens age out and capacity returns."""
    from alpaca_rate_guard import RateGuard
    g = RateGuard(max_per_min=5)
    # Inject 5 ancient timestamps (>60s ago)
    g._timestamps.extend([time.monotonic() - 70.0] * 5)
    # Capacity is now full of stale entries; can_proceed should clean them
    ok, _ = g.can_proceed()
    assert ok is True
    assert g.current_rate_per_min == 0


def test_probe_ws_slot_free_returns_true_on_auth_success(monkeypatch):
    """When alpaca SDK's _auth() succeeds, probe returns (True, 'auth ok')."""
    import alpaca_rate_guard as arg

    fake_stream = MagicMock()
    fake_stream._connect = AsyncMock()
    fake_stream._auth = AsyncMock()
    fake_stream.close = AsyncMock()

    class FakeSDS:
        def __init__(self, *a, **kw):
            pass
        def __new__(cls, *a, **kw):
            return fake_stream

    # patch the import inside the function
    import alpaca.data.live as live_mod
    monkeypatch.setattr(live_mod, "StockDataStream", FakeSDS)

    ok, detail = asyncio.run(arg.probe_ws_slot_free(
        api_key="k", api_secret="s"))
    assert ok is True
    assert "auth ok" in detail


def test_probe_ws_slot_free_returns_false_on_conn_limit(monkeypatch):
    import alpaca_rate_guard as arg

    fake_stream = MagicMock()
    fake_stream._connect = AsyncMock()
    fake_stream._auth = AsyncMock(side_effect=ValueError("connection limit exceeded"))
    fake_stream.close = AsyncMock()

    class FakeSDS:
        def __init__(self, *a, **kw):
            pass
        def __new__(cls, *a, **kw):
            return fake_stream

    import alpaca.data.live as live_mod
    monkeypatch.setattr(live_mod, "StockDataStream", FakeSDS)

    ok, detail = asyncio.run(arg.probe_ws_slot_free(
        api_key="k", api_secret="s"))
    assert ok is False
    assert "connection limit" in detail.lower()


def test_wait_until_ws_slot_free_polls_then_succeeds(monkeypatch):
    """Probe 3 times: fail, fail, succeed. Must return (True, attempts=3)."""
    import alpaca_rate_guard as arg
    results = iter([
        (False, "ValueError: connection limit exceeded"),
        (False, "ValueError: connection limit exceeded"),
        (True, "auth ok"),
    ])

    async def fake_probe(**kw):
        return next(results)

    monkeypatch.setattr(arg, "probe_ws_slot_free", fake_probe)
    # Patch asyncio.sleep to no-op so test doesn't wait 15s
    orig_sleep = asyncio.sleep

    async def fast_sleep(t):
        await orig_sleep(0)

    monkeypatch.setattr(arg.asyncio, "sleep", fast_sleep)

    ok, attempts, detail = asyncio.run(arg.wait_until_ws_slot_free(
        api_key="k", api_secret="s",
        max_wait_sec=60, interval_sec=1,
    ))
    assert ok is True
    assert attempts == 3
    assert "auth ok" in detail


def test_wait_until_ws_slot_free_times_out(monkeypatch):
    """If probe never succeeds within max_wait_sec, returns (False, ...)."""
    import alpaca_rate_guard as arg

    async def always_locked(**kw):
        return False, "locked"

    monkeypatch.setattr(arg, "probe_ws_slot_free", always_locked)
    orig_sleep = asyncio.sleep

    async def fast_sleep(t):
        await orig_sleep(0)

    monkeypatch.setattr(arg.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(arg.time, "monotonic", lambda c=[0.0]:
                         (c.__setitem__(0, c[0] + 0.5) or c[0]))

    ok, attempts, detail = asyncio.run(arg.wait_until_ws_slot_free(
        api_key="k", api_secret="s",
        max_wait_sec=2, interval_sec=1,
    ))
    assert ok is False
    assert attempts >= 1
    assert "locked" in detail.lower()


def test_ws_patch_invokes_wait_until_ws_slot_free_on_conn_limit():
    """Source-grep: alpaca_ws_patch.py must invoke wait_until_ws_slot_free
    when it sees 'connection limit'."""
    src = (ROOT / "06_live_bot" / "alpaca_ws_patch.py").read_text(encoding="utf-8")
    assert "wait_until_ws_slot_free" in src
    assert "connection limit" in src.lower()
    # The probe-then-continue branch must precede the fall-through sleep
    probe_idx = src.find("wait_until_ws_slot_free(")
    sleep_idx = src.find("await asyncio.sleep(sleep_for)")
    assert probe_idx > 0
    assert sleep_idx > probe_idx
