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
import threading
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

# Phase-43 (user request 2026-05-15): "soll der bot nur eine instanz
# global singleton machen für WS access und der soll auch meckern
# wenn er missbraucht wird".
#
# Why this is the real architectural fix (not another "patch"):
# bot.py outer ws_loop does:
#   ws = StockDataStream(...)                # NEW instance every iter
#   ws.subscribe_bars(on_bar, *symbols)
#   ws_task = asyncio.to_thread(ws.run)
#   while ...: if resubscribe_needed: ws.stop_ws(); break
#   (loop back to top, create ANOTHER new instance)
#
# Alpaca's paper account has 1 WS slot per API key. Two simultaneous
# StockDataStream instances both try to auth → "connection limit
# exceeded". The previous patches (Phase-31..42) tried to MITIGATE
# this with backoff + cool-down, but the architectural problem is
# bot.py spawning multiple instances. Phase-43 enforces the invariant
# directly: only ONE StockDataStream per process. Misuse is logged
# loudly so the bot author can see + fix the caller pattern.
_ws_singleton = None                # the one and only StockDataStream
_ws_singleton_lock = threading.Lock()
_ws_abuse_count = 0                  # total duplicate-construct calls caught
# Originals captured at first enable_ws_singleton() so _reset_for_tests
# can robustly restore them even if multiple install/reset cycles happen.
_original_sds_new = None
_original_sds_init = None

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
                # Phase-78 (2026-05-19): reduce WS-storm log noise. In a
                # 1622×-event day, the same "ws backoff … TimeoutError
                # (consec=6)" line repeated 1622 times — operator can't
                # see useful events through the spam. Now: WARNING only
                # on (a) first occurrence in a consec sequence (consec=1)
                # AND (b) the cap-out boundary (consec=6). All others
                # demoted to DEBUG. This preserves the storm-signal but
                # caps cost at ~2 lines per cycle instead of 1 per retry.
                err_cls = type(e).__name__
                last_err_cls = getattr(self, "_phase78_last_err_cls", None)
                err_cls_changed = err_cls != last_err_cls
                if (consec_value_errors == 1 or
                        consec_value_errors == 6 or
                        err_cls_changed):
                    log.warning("ws backoff %.1fs after %s (consec=%d)",
                                 sleep_for, err_cls, consec_value_errors)
                else:
                    log.debug("ws backoff %.1fs after %s (consec=%d)",
                               sleep_for, err_cls, consec_value_errors)
                self._phase78_last_err_cls = err_cls
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


def enable_ws_singleton() -> bool:
    """Phase-43: singleton-enforce StockDataStream construction.

    SEPARATE from install_patch() so that test code which imports bot
    (and thus triggers install_patch()) doesn't also enforce the
    singleton — many tests legitimately create multiple StockDataStream
    instances or expect __init__ errors that the singleton would swallow.

    Production: call this once at bot startup, AFTER install_patch().
    Tests: do NOT call this except in dedicated Phase-43 tests.

    Wraps __new__ + __init__ at class level. Idempotent.
    """
    try:
        from alpaca.data.live.stock import StockDataStream as _SDS
    except Exception:
        log.warning("alpaca-py StockDataStream not importable; singleton skipped")
        return False

    if getattr(_SDS, "_phase43_singleton_installed", False):
        return True

    global _original_sds_new, _original_sds_init
    # Capture originals only the FIRST time enable runs in this process.
    # On re-enable after a _reset_for_tests, we want to wrap the TRUE
    # originals, not whatever the prior reset left.
    if _original_sds_new is None:
        _original_sds_new = _SDS.__dict__.get("__new__", None)
    if _original_sds_init is None:
        _original_sds_init = _SDS.__dict__.get("__init__", None)
    original_new = _SDS.__new__
    original_init = _SDS.__init__

    def singleton_new(cls, *a, **kw):
        global _ws_abuse_count
        with _ws_singleton_lock:
            if _ws_singleton is not None:
                _ws_abuse_count += 1
                # Mark instance so __init__ skips re-init
                _ws_singleton._phase43_skip_init = True
                # Caller stack so the abuse is debuggable
                import traceback as _tb
                caller = "".join(_tb.format_stack(limit=4)[:-1])
                log.warning(
                    "WS SINGLETON ABUSED — duplicate StockDataStream() "
                    "construction (#%d). Returning existing instance. "
                    "Caller:\n%s",
                    _ws_abuse_count, caller,
                )
                return _ws_singleton
            # First construction — let __new__ run. NOTE: we do NOT
            # cache the singleton here. __init__ might raise (e.g.
            # invalid feed kwarg), in which case the half-baked
            # instance would poison the cache. Caching is moved to
            # singleton_init AFTER original_init() returns cleanly.
            if original_new is object.__new__:
                return original_new(cls)
            return original_new(cls, *a, **kw)

    def singleton_init(self, *a, **kw):
        global _ws_singleton
        if getattr(self, "_phase43_skip_init", False):
            # Clear the flag and short-circuit so we don't
            # re-init the cached singleton (would reset _handlers
            # and lose existing subscriptions).
            self._phase43_skip_init = False
            return
        original_init(self, *a, **kw)
        # Cache ONLY after successful init. If original_init raised,
        # this line never executes -> no caching of broken instance.
        with _ws_singleton_lock:
            if _ws_singleton is None:
                _ws_singleton = self

    singleton_new._phase43_original = original_new        # type: ignore[attr-defined]
    singleton_init._phase43_original = original_init      # type: ignore[attr-defined]
    _SDS.__new__ = singleton_new
    _SDS.__init__ = singleton_init
    _SDS._phase43_singleton_installed = True
    log.info("StockDataStream singleton enforcement installed (Phase-43)")
    return True


def is_patched() -> bool:
    return _PATCHED


def is_singleton_enabled() -> bool:
    try:
        from alpaca.data.live.stock import StockDataStream as _SDS
        return bool(getattr(_SDS, "_phase43_singleton_installed", False))
    except Exception:
        return False


def reset_ws_singleton() -> None:
    """Clear the singleton so the next StockDataStream() construction
    creates a fresh instance. Call this AFTER intentionally tearing down
    the live WS (e.g. on HARD_FLAT, bot shutdown). Do NOT call on a
    transient watchlist resubscribe — that's what Phase-43 protects against."""
    global _ws_singleton
    with _ws_singleton_lock:
        _ws_singleton = None


def get_ws_abuse_count() -> int:
    """How many duplicate-construct calls have been intercepted since
    process start. Useful for tests + dashboards."""
    return _ws_abuse_count


def _reset_for_tests() -> None:
    """ONLY for unit tests — restore originals."""
    global _PATCHED, _global_cool_down_until, _ws_singleton, _ws_abuse_count
    _global_cool_down_until = 0.0
    _ws_singleton = None
    _ws_abuse_count = 0
    # Undo _run_forever patch (Phase-31)
    try:
        from alpaca.data.live import websocket as ws_module
        cur = ws_module.DataStream._run_forever
        original = getattr(cur, "_unpatched", None)
        if original is not None:
            ws_module.DataStream._run_forever = original
    except Exception:
        pass
    # Undo singleton wrapping (Phase-43). CPython caches type slots
    # (tp_new in particular) and once StockDataStream.__new__ is
    # monkey-patched, deletion / reassignment can leave the slot in
    # a state where object.__new__ rejects extra args. The reliable
    # workaround is to RELOAD the module so the class is freshly
    # constructed from source — this fully clears the slot cache.
    try:
        from alpaca.data.live.stock import StockDataStream as _SDS
        if getattr(_SDS, "_phase43_singleton_installed", False):
            _SDS._phase43_singleton_installed = False
            import importlib
            import alpaca.data.live.stock as _stock_mod
            importlib.reload(_stock_mod)
    except Exception:
        pass
    _PATCHED = False
