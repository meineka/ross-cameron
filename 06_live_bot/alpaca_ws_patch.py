"""Phase-31: alpaca-py WebSocket reconnect-backoff patch.

The vendored `alpaca.data.live.websocket._run_forever` retries on every
`ValueError` (auth failures, "connection limit exceeded", etc.) WITHOUT
any sleep:

    except ValueError as ve:
        if "insufficient subscription" in str(ve):
            return  # special case, stop loop
        log.exception(...)
        # falls through to finally: asyncio.sleep(0)   ← zero wait
        # ... loop iterates ~600ms later

Result: when Alpaca's paper-account stale-WS-slot lockout fires
("connection limit exceeded"), the bot hammers the auth endpoint ~1.6x
per second indefinitely, generating thousands of log lines and likely
extending the lockout itself.

This module monkey-patches the WS class's `_run_forever` (or wraps it)
to add exponential backoff on ANY ValueError. On the "connection limit
exceeded" specifically, it sleeps at LEAST RESET_SLOT_SEC (default 30s)
because Alpaca's documented stale-slot release is ~5min, and 30s gives
the server breathing room without losing too many bars.

Idempotent — calling `install_patch()` twice has no effect.
"""
from __future__ import annotations
import asyncio
import logging
import time

log = logging.getLogger("alpaca-ws-patch")

# Backoff config — tuned for paper-account "connection limit exceeded".
# Real-account auth failures (bad API key) propagate via the outer
# bot.py ws_loop which has its own circuit-breaker (max_consec_fails=8).
BASE_SLEEP_SEC = 2.0
CAP_SLEEP_SEC = 60.0
RESET_SLOT_SEC = 30.0  # minimum sleep on "connection limit exceeded"

# Phase-41 (2026-05-15): empirical Alpaca-paper-account session-linger
# is > 60s — exponential ramp 5/10/20/40s kept the slot locked because
# each retry reset Alpaca's server-side timer BEFORE it expired.
# Explicit schedule guarantees gaps wider than the linger window
# starting on the second attempt. Cost: ~1.5 attempts/min during a
# stall, well under the 200/min rate cap.
CONN_LIMIT_SLEEP_SCHEDULE = [5, 60, 120, 180, 300]  # seconds per consec idx

# Phase-42 (2026-05-15): module-global cool-down so every NEW
# StockDataStream instance (bot.py creates fresh ones on watchlist
# changes — each has its own consec counter starting at 0) respects
# the recent connection-limit-exceeded state. Without this, instance
# A fails -> instance B spawns 5 seconds later -> instance B fails
# -> instance C 5s later. Net: 5s cadence even though Phase-41 schedule
# is 60+s per-instance.
COOL_DOWN_AFTER_CONN_LIMIT_SEC = 90  # > observed Alpaca session-linger (60s)
_global_cool_down_until: float = 0.0  # time.monotonic() value

# Phase-35 (user request 2026-05-15): retry cadence on "connection
# limit exceeded" comes from alpaca_rate_guard.
#
# Phase-38 (2026-05-15): the original probe-every-5s logic was REMOVED
# because the probe itself opened a WS connection that held Alpaca's
# slot for ~30s server-side after close — self-defeating. The 5-sec
# cadence is preserved, but applied as a DIRECT sleep-and-retry on the
# existing ws instance (no separate probe).
try:
    from alpaca_rate_guard import ALPACA_STALL_PROBE_INTERVAL_SEC
    _STALL_PROBE_AVAILABLE = True
except Exception:
    _STALL_PROBE_AVAILABLE = False
    ALPACA_STALL_PROBE_INTERVAL_SEC = 5

_PATCHED = False


def install_patch() -> bool:
    """Install the backoff patch. Returns True on success, False if the
    SDK shape has changed and the patch could not safely apply."""
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from alpaca.data.live import websocket as ws_module
    except Exception as e:
        log.warning("alpaca-py not importable; cannot patch: %s", e)
        return False

    if not hasattr(ws_module, "DataStream"):
        log.warning("alpaca.data.live.websocket.DataStream missing; "
                     "SDK shape changed — skipping patch")
        return False

    original = ws_module.DataStream._run_forever

    async def patched_run_forever(self):
        """Same control flow as upstream but with backoff on ValueError."""
        self._loop = asyncio.get_running_loop()
        # Wait until at least one subscription exists (upstream pattern)
        while not any(
            v for k, v in self._handlers.items()
            if k not in ("cancelErrors", "corrections")
        ):
            if not self._stop_stream_queue.empty():
                self._stop_stream_queue.get(timeout=1)
                return
            await asyncio.sleep(0)
        log.info("started %s stream (patched)", getattr(self, "_name", "?"))
        self._should_run = True
        self._running = False
        consec_value_errors = 0
        while True:
            try:
                if not self._should_run:
                    log.info("%s stream stopped", getattr(self, "_name", "?"))
                    return
                if not self._running:
                    # Phase-42: respect the module-global cool-down BEFORE
                    # any new auth attempt. This prevents fresh
                    # StockDataStream instances (created by bot.py on
                    # watchlist changes) from circumventing the per-instance
                    # backoff and hammering Alpaca every 5s.
                    import alpaca_ws_patch as _mod
                    cool_left = _mod._global_cool_down_until - time.monotonic()
                    if cool_left > 0:
                        log.warning("cool-down active: sleeping %.1fs before "
                                     "next auth (slot recently locked)",
                                     cool_left)
                        try:
                            await asyncio.sleep(cool_left)
                        except asyncio.CancelledError:
                            return
                    log.info("starting %s websocket connection",
                              getattr(self, "_name", "?"))
                    await self._start_ws()
                    await self._send_subscribe_msg()
                    self._running = True
                    consec_value_errors = 0  # success → reset
                await self._consume()
            except Exception as e:
                # Treat all exceptions the same way upstream does, but
                # add backoff. Only "insufficient subscription" is a
                # hard stop (matches upstream's special-case).
                is_value = isinstance(e, ValueError)
                if is_value and "insufficient subscription" in str(e):
                    try:
                        await self.close()
                    except Exception:
                        pass
                    self._running = False
                    log.exception("error during websocket communication: %s", e)
                    return
                log.exception("error during websocket communication: %s", e)
                # Force-close the socket so the next iteration does a
                # clean reconnect. Upstream skipped this for ValueError
                # which is why the slot lockout never released.
                try:
                    await self.close()
                except Exception:
                    pass
                self._running = False
                # Pick sleep duration / retry strategy.
                # Phase-41 (2026-05-15): use the explicit schedule
                # CONN_LIMIT_SLEEP_SCHEDULE. Empirical observation:
                # Alpaca-paper session-linger is > 60s, so the previous
                # exponential ramp 5/10/20/40s kept resetting the
                # server-side timer. New schedule: 5s first (per user
                # spec "alle 5 Sekunden" for first stall-detection
                # attempt), then jumps to 60/120/180/300s to guarantee
                # gap > linger.
                msg = str(e)
                if "connection limit" in msg.lower():
                    # Phase-42: set module-global cool-down so any new
                    # StockDataStream instance respects this state
                    import alpaca_ws_patch as _mod
                    _mod._global_cool_down_until = (
                        time.monotonic() + COOL_DOWN_AFTER_CONN_LIMIT_SEC)
                    idx = min(consec_value_errors, len(CONN_LIMIT_SLEEP_SCHEDULE) - 1)
                    sleep_for = float(CONN_LIMIT_SLEEP_SCHEDULE[idx])
                elif is_value:
                    sleep_for = min(CAP_SLEEP_SEC,
                                     BASE_SLEEP_SEC * (2 ** consec_value_errors))
                else:
                    # websocket errors etc. — short sleep, the SDK
                    # already handled the close() side
                    sleep_for = min(CAP_SLEEP_SEC,
                                     BASE_SLEEP_SEC * (2 ** consec_value_errors))
                consec_value_errors = min(consec_value_errors + 1, 6)
                log.warning("ws backoff %.1fs after %s (consec=%d)",
                             sleep_for, type(e).__name__, consec_value_errors)
                try:
                    await asyncio.sleep(sleep_for)
                except asyncio.CancelledError:
                    return
            finally:
                # Tiny yield so other tasks run
                await asyncio.sleep(0)

    # Save original for potential rollback during tests
    patched_run_forever._unpatched = original  # type: ignore[attr-defined]
    ws_module.DataStream._run_forever = patched_run_forever
    _PATCHED = True
    log.info("alpaca-py DataStream._run_forever patched with backoff")
    return True


def is_patched() -> bool:
    return _PATCHED


def _reset_for_tests() -> None:
    """ONLY for unit tests — restore original."""
    global _PATCHED, _global_cool_down_until
    _global_cool_down_until = 0.0  # Phase-42: clear leaked state between tests
    if not _PATCHED:
        return
    try:
        from alpaca.data.live import websocket as ws_module
        cur = ws_module.DataStream._run_forever
        original = getattr(cur, "_unpatched", None)
        if original is not None:
            ws_module.DataStream._run_forever = original
    except Exception:
        pass
    _PATCHED = False
