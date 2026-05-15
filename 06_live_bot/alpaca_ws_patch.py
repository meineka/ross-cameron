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

# Phase-35 (user request 2026-05-15): on "connection limit exceeded",
# stop sleeping blindly and instead probe the WS slot every 5 sec
# until it's free, then reconnect immediately.
try:
    from alpaca_rate_guard import (
        wait_until_ws_slot_free,
        ALPACA_STALL_PROBE_INTERVAL_SEC,
    )
    _STALL_PROBE_AVAILABLE = True
except Exception:
    _STALL_PROBE_AVAILABLE = False

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
                # Pick sleep duration / stall-probe strategy
                msg = str(e)
                if "connection limit" in msg.lower() and _STALL_PROBE_AVAILABLE:
                    # Phase-35: instead of sleeping 30-60s blindly, probe
                    # the WS slot every 5 sec until it's free, then
                    # reconnect immediately. Caps the wait at 5 min so
                    # we eventually surrender + let outer ws_loop apply
                    # its own circuit-breaker.
                    log.warning("WS slot locked — probing every %ds "
                                 "until free (consec=%d)",
                                 ALPACA_STALL_PROBE_INTERVAL_SEC,
                                 consec_value_errors)
                    try:
                        api_key = getattr(self, "_api_key", "")
                        api_secret = getattr(self, "_secret_key", "")
                        feed = "iex"
                        # feed name from endpoint URL like ".../v2/iex"
                        ep = getattr(self, "_endpoint", "")
                        if "/sip" in ep.lower():
                            feed = "sip"
                        ok, attempts, detail = await wait_until_ws_slot_free(
                            api_key=api_key, api_secret=api_secret,
                            max_wait_sec=300,
                            interval_sec=ALPACA_STALL_PROBE_INTERVAL_SEC,
                        )
                        if ok:
                            log.info("WS slot released after %d probes (%s) — "
                                     "reconnecting now",
                                     attempts, detail)
                            consec_value_errors = 0
                            # Skip the sleep_for fallback — loop top will
                            # reconnect on next iteration
                            continue
                        # probe timeout: fall through to legacy sleep
                        log.warning("WS slot still locked after %d probes "
                                     "— falling back to blind sleep",
                                     attempts)
                    except Exception as probe_err:
                        log.warning("stall-probe crashed (%s) — falling back",
                                     probe_err)
                    sleep_for = max(RESET_SLOT_SEC,
                                     min(CAP_SLEEP_SEC,
                                         BASE_SLEEP_SEC * (2 ** consec_value_errors)))
                elif "connection limit" in msg.lower():
                    sleep_for = max(RESET_SLOT_SEC,
                                     min(CAP_SLEEP_SEC,
                                         BASE_SLEEP_SEC * (2 ** consec_value_errors)))
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
    global _PATCHED
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
