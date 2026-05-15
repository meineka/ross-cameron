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


def test_global_cool_down_blocks_new_instance_auth():
    """Phase-42: module-global cool-down ensures a freshly-spawned
    StockDataStream (which has consec=0 by default) cannot bypass the
    per-instance backoff when Alpaca's slot was recently locked.

    Sets _global_cool_down_until = now+10s. A new patched_run_forever
    instance must sleep ~10s before its first _start_ws() call."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module
    import time as _t

    # Pre-set the cool-down to 8 seconds from now
    alpaca_ws_patch._global_cool_down_until = _t.monotonic() + 8

    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)
        # After the cool-down sleep, kill the loop so we don't hit _start_ws
        if t >= 5.0:
            stream._should_run = False

    stream = MagicMock()
    stream._handlers = {"bars": ["AAA"]}
    stream._stop_stream_queue = MagicMock()
    stream._stop_stream_queue.empty.return_value = True
    stream._should_run = True
    stream._running = False
    stream._name = "test"
    stream._loop = None
    stream._start_ws = AsyncMock()
    stream._send_subscribe_msg = AsyncMock()
    stream._consume = AsyncMock()
    stream.close = AsyncMock()

    async def run_test():
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)

    asyncio.run(run_test())
    # The cool-down sleep (~8s) must have fired BEFORE any _start_ws call
    cool_down_sleeps = [s for s in sleeps if 5 < s < 12]
    assert cool_down_sleeps, (
        f"expected ~8s cool-down sleep, got {sleeps[:5]}"
    )
    alpaca_ws_patch._global_cool_down_until = 0.0
    alpaca_ws_patch._reset_for_tests()


def test_conn_limit_failure_sets_global_cool_down():
    """Phase-42: when patched _run_forever sees 'connection limit
    exceeded', it sets _global_cool_down_until = now + 90s."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch._global_cool_down_until = 0.0
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module
    import time as _t

    async def fake_sleep(t):
        # Stop after first backoff
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

    t_before = _t.monotonic()
    async def run_test():
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)
    asyncio.run(run_test())

    cd = alpaca_ws_patch._global_cool_down_until
    # Should be set to t_before + 90 (give or take small clock drift)
    assert cd > t_before + 80, f"cool-down too short: {cd - t_before}"
    assert cd < t_before + 100, f"cool-down too long: {cd - t_before}"
    alpaca_ws_patch._global_cool_down_until = 0.0
    alpaca_ws_patch._reset_for_tests()


def test_ws_singleton_first_construction_succeeds():
    """First StockDataStream() call creates and stores the singleton."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    alpaca_ws_patch.enable_ws_singleton()
    from alpaca.data.live.stock import StockDataStream
    ws = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    assert alpaca_ws_patch._ws_singleton is ws
    assert alpaca_ws_patch.get_ws_abuse_count() == 0
    alpaca_ws_patch._reset_for_tests()


def test_ws_singleton_second_construction_returns_existing_and_logs():
    """Second+ StockDataStream() call:
      - returns the EXISTING instance (not a new one)
      - increments abuse counter
      - logs 'WS SINGLETON ABUSED' warning
    Caller code that does `ws = StockDataStream(...)` keeps working but
    the misuse is visible in the log."""
    import alpaca_ws_patch
    import logging as _l
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    alpaca_ws_patch.enable_ws_singleton()
    from alpaca.data.live.stock import StockDataStream
    ws1 = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    # Capture log output
    handler_records = []
    class _CapH(_l.Handler):
        def emit(self, rec):
            handler_records.append(self.format(rec))
    h = _CapH()
    h.setLevel(_l.WARNING)
    _l.getLogger("alpaca-ws-patch").addHandler(h)
    try:
        ws2 = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)  # 2nd construction
    finally:
        _l.getLogger("alpaca-ws-patch").removeHandler(h)
    assert ws2 is ws1, "2nd construction must return the existing singleton"
    assert alpaca_ws_patch.get_ws_abuse_count() == 1
    assert any("SINGLETON ABUSED" in m for m in handler_records), \
        f"Expected 'SINGLETON ABUSED' warning, got: {handler_records}"
    alpaca_ws_patch._reset_for_tests()


def test_ws_singleton_init_does_not_re_init_existing_instance():
    """After 2nd construction, the existing singleton's state (e.g.
    _handlers from a previous subscribe_bars call) must be PRESERVED."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    alpaca_ws_patch.enable_ws_singleton()
    from alpaca.data.live.stock import StockDataStream
    ws1 = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    # Simulate existing subscription
    ws1._handlers["bars"] = ["AAA", "BBB"]
    ws2 = StockDataStream("k2", "s2", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    assert ws2 is ws1
    # Subscriptions survived the 2nd "construct"
    assert ws1._handlers.get("bars") == ["AAA", "BBB"]
    alpaca_ws_patch._reset_for_tests()


def test_ws_singleton_reset_lets_next_construction_create_fresh():
    """reset_ws_singleton() clears the cache so the next construct
    actually creates a new instance. Use after legitimate teardown."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    alpaca_ws_patch.enable_ws_singleton()
    from alpaca.data.live.stock import StockDataStream
    ws1 = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    alpaca_ws_patch.reset_ws_singleton()
    assert alpaca_ws_patch._ws_singleton is None
    ws2 = StockDataStream("k", "s", feed=__import__("alpaca.data.enums", fromlist=["DataFeed"]).DataFeed.IEX)
    assert ws2 is not ws1
    alpaca_ws_patch._reset_for_tests()


def test_ws_singleton_install_is_idempotent():
    """install_patch() called twice must not double-wrap __new__/__init__."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    alpaca_ws_patch.enable_ws_singleton()
    from alpaca.data.live.stock import StockDataStream
    new_after_first = StockDataStream.__new__
    alpaca_ws_patch.install_patch()
    new_after_second = StockDataStream.__new__
    assert new_after_first is new_after_second
    alpaca_ws_patch._reset_for_tests()


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
    """Phase-38 (2026-05-15) FIX: on 'connection limit exceeded' the
    patch now sleeps 5s and retries on the EXISTING ws instance instead
    of running a separate probe that itself consumes the slot.

    The old Phase-35 probe-every-5s was self-defeating: each probe
    opened its own _connect+_auth, briefly held the slot, then closed.
    Alpaca held the slot ~30s server-side post-close, so the next probe
    always saw it locked. We were locking the slot we were checking.

    Now: 5s sleep, then retry on self._start_ws() (next loop iter).
    No extra WS clients spawned."""
    import alpaca_ws_patch
    alpaca_ws_patch._reset_for_tests()
    alpaca_ws_patch.install_patch()
    from alpaca.data.live import websocket as ws_module

    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)
        # After 2 backoff sleeps (>=1s) stop the loop
        if sum(1 for s in sleeps if s >= 1.0) >= 2:
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
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await ws_module.DataStream._run_forever(stream)

    asyncio.run(run_test())

    # Phase-41 assertion: first retry 5s (user spec), then 60s (jumps
    # OVER Alpaca's > 60s session-linger to break out of the slot-lock
    # loop). Schedule continues 120/180/300 on persistent failure.
    from alpaca_ws_patch import CONN_LIMIT_SLEEP_SCHEDULE
    assert CONN_LIMIT_SLEEP_SCHEDULE[0] == 5
    assert CONN_LIMIT_SLEEP_SCHEDULE[1] >= 60
    backoff_sleeps = [s for s in sleeps if s >= 1.0]
    assert len(backoff_sleeps) >= 2, f"expected >=2 backoff sleeps, got {backoff_sleeps}"
    assert backoff_sleeps[0] == 5.0, \
        f"expected 5s on FIRST conn-limit retry, got {backoff_sleeps[0]}s"
    assert backoff_sleeps[1] >= 60.0, (
        f"expected SECOND retry >= 60s (Alpaca linger), got {backoff_sleeps[1]}s"
    )
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
