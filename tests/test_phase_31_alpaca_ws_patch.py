"""Phase-31: alpaca-py WebSocket reconnect-backoff patch tests.

The vendored alpaca-py SDK's `_run_forever` retries on every `ValueError`
with effectively zero sleep (just `asyncio.sleep(0)` in the finally
block). When Alpaca's paper-account hits the stale-WS-slot lockout
("connection limit exceeded"), the bot used to spam 1.6 reconnect
attempts per second indefinitely.

This patch installs a backoff that sleeps:
  - >= 30s on "connection limit exceeded"
  - exponential 2s -> 60s on any other ValueError
  - and closes the socket between attempts (upstream didn't)

The patch is idempotent.
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_install_patch_is_idempotent():
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    assert alpaca_ws_patch.is_patched() is False
    assert alpaca_ws_patch.install_patch() is True
    assert alpaca_ws_patch.is_patched() is True
    # Re-install must NOT double-wrap
    assert alpaca_ws_patch.install_patch() is True
    assert alpaca_ws_patch.is_patched() is True
    alpaca_ws_patch._reset_for_tests()


def test_patch_replaces_run_forever():
    import alpaca_ws_patch
    from alpaca.data.live import websocket as ws_module
    alpaca_ws_patch._reset_for_tests()
    original = ws_module.DataStream._run_forever
    alpaca_ws_patch.install_patch()
    patched = ws_module.DataStream._run_forever
    assert patched is not original
    # The unpatched original is preserved on the patched fn
    assert getattr(patched, "_unpatched", None) is original
    alpaca_ws_patch._reset_for_tests()
    assert ws_module.DataStream._run_forever is original


def test_patched_loop_handles_connection_limit_exceeded():
    """Phase-35 (2026-05-15) changed this branch: instead of sleeping 30-60s
    on 'connection limit exceeded', the patch now calls
    wait_until_ws_slot_free (probe every 5s). Verify the probe IS invoked
    when the stall-probe module is available. The fall-through legacy
    sleep is exercised by test_patched_loop_uses_exponential_backoff_on_generic_value_error."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module
    import alpaca_rate_guard

    probe_calls = []

    async def fake_wait(**kw):
        probe_calls.append(kw)
        # Pretend slot stays locked → falls through to legacy sleep
        return False, 1, "still locked"

    async def fake_sleep(t):
        # Stop the loop after the fall-through sleep
        if t >= 1.0:
            stream._should_run = False

    stream = MagicMock()
    stream._handlers = {"bars": ["AAA"]}
    stream._stop_stream_queue = MagicMock()
    stream._stop_stream_queue.empty.return_value = True
    stream._should_run = True
    stream._running = False
    stream._name = "test"
    stream._loop = None
    stream._api_key = "k"
    stream._secret_key = "s"
    stream._endpoint = "wss://stream.data.alpaca.markets/v2/iex"
    stream._start_ws = AsyncMock(side_effect=ValueError("connection limit exceeded"))
    stream._send_subscribe_msg = AsyncMock()
    stream._consume = AsyncMock()
    stream.close = AsyncMock()

    async def run_test():
        with patch.object(alpaca_ws_patch, "wait_until_ws_slot_free",
                          side_effect=fake_wait), \
             patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)

    asyncio.run(run_test())

    # The CRITICAL assertion (Phase-35): wait_until_ws_slot_free was invoked
    # with the 5-sec interval and the stream's credentials.
    assert probe_calls, "wait_until_ws_slot_free was NOT called on connection-limit"
    assert probe_calls[0]["interval_sec"] == 5
    assert probe_calls[0]["api_key"] == "k"
    alpaca_ws_patch._reset_for_tests()


def test_patched_loop_uses_exponential_backoff_on_generic_value_error():
    """Generic ValueError should backoff exponentially from 2s."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module

    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)
        # Stop after 3 backoff sleeps (sleeps>=1.0)
        if sum(1 for s in sleeps if s >= 1.0) >= 3:
            stream._should_run = False

    stream = MagicMock()
    stream._handlers = {"bars": ["AAA"]}
    stream._stop_stream_queue = MagicMock()
    stream._stop_stream_queue.empty.return_value = True
    stream._should_run = True
    stream._running = False
    stream._name = "test"
    stream._loop = None
    stream._start_ws = AsyncMock(side_effect=ValueError("generic auth fail"))
    stream._send_subscribe_msg = AsyncMock()
    stream._consume = AsyncMock()
    stream.close = AsyncMock()

    async def run_test():
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)

    asyncio.run(run_test())

    backoff = [s for s in sleeps if s >= 1.0]
    assert len(backoff) >= 2, f"expected >=2 backoffs, got {backoff}"
    # First sleep is 2s base, second is doubled (4s)
    assert backoff[0] >= 2.0
    assert backoff[1] >= backoff[0]  # monotonic increase
    alpaca_ws_patch._reset_for_tests()


def test_patched_loop_closes_socket_before_retry():
    """Upstream didn't call close() on ValueError → dangling socket. Patch must."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module

    async def fake_sleep(t):
        if t >= 1.0:
            stream._should_run = False

    stream = MagicMock()
    stream._handlers = {"bars": ["AAA"]}
    stream._stop_stream_queue = MagicMock()
    stream._stop_stream_queue.empty.return_value = True
    stream._should_run = True
    stream._running = False
    stream._name = "test"
    stream._loop = None
    stream._start_ws = AsyncMock(side_effect=ValueError("connection limit exceeded"))
    stream._send_subscribe_msg = AsyncMock()
    stream._consume = AsyncMock()
    stream.close = AsyncMock()

    async def run_test():
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)

    asyncio.run(run_test())
    stream.close.assert_called()
    alpaca_ws_patch._reset_for_tests()


def test_patched_loop_still_stops_on_insufficient_subscription():
    """The upstream special-case must still terminate the loop."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module

    stream = MagicMock()
    stream._handlers = {"bars": ["AAA"]}
    stream._stop_stream_queue = MagicMock()
    stream._stop_stream_queue.empty.return_value = True
    stream._should_run = True
    stream._running = False
    stream._name = "test"
    stream._loop = None
    stream._start_ws = AsyncMock(side_effect=ValueError("insufficient subscription"))
    stream._send_subscribe_msg = AsyncMock()
    stream._consume = AsyncMock()
    stream.close = AsyncMock()

    async def run_test():
        await asyncio.wait_for(
            ws_module.DataStream._run_forever(stream), timeout=2.0,
        )

    asyncio.run(run_test())
    stream.close.assert_called()
    alpaca_ws_patch._reset_for_tests()


def test_bot_imports_and_installs_patch():
    """Source-grep: bot.py imports + installs the patch at module load."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from alpaca_ws_patch import install_patch" in src
    assert "_install_alpaca_ws_patch()" in src
