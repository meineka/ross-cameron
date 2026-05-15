"""Phase-35: Alpaca API rate-guard + stall-detect probe.

USER REQUEST 2026-05-15: "max alpaca calls 200 per min, wenn stalled,
testcall alle 5 Sekunden. Nimm das in die Logik auf."

Two coupled features both backed by Alpaca's documented 200 req/min
account-wide limit:

  1. RateGuard  — token-bucket cap: any call that would exceed the
                  budget is BLOCKED until a slot frees up. Use as a
                  context manager around every outbound Alpaca call.
  2. StallProbe — when the WebSocket data feed hits repeated
                  "connection limit exceeded" auth failures, cheap
                  REST probes fire every PROBE_INTERVAL_SEC seconds
                  to detect when the WS slot is actually free
                  server-side — no more guessing, no more 60-second
                  sleeps that overshoot.

Both pieces are PROCESS-LOCAL (in-memory). Multi-process scenarios are
not in scope; the watchdog already prevents that.
"""
from __future__ import annotations
import asyncio
import logging
import threading
import time
from collections import deque

log = logging.getLogger("alpaca-rate-guard")

# ─── Single-source-of-truth Alpaca limits ──────────────────────────────────
# These are documented Alpaca-account-wide limits. Read by both
# alpaca_ws_patch and the outer bot.ws_loop. Override via the bot.py
# constants of the same name (see bot.py setup section).
ALPACA_MAX_CALLS_PER_MIN = 200
ALPACA_STALL_PROBE_INTERVAL_SEC = 5
# Detection threshold: how many CONSECUTIVE "connection limit exceeded"
# failures count as "stalled" and trigger the 5-sec probe loop.
ALPACA_STALL_AFTER_N_FAILS = 1   # one failure is enough — slot is locked
# Safety budget for probe calls themselves so the probe doesn't itself
# blow the rate limit. 5-sec interval = 12 probes/min, well under 200.
PROBE_MAX_PER_MIN = 20


class RateGuard:
    """Token-bucket rate limiter for Alpaca calls.

    Use as a (sync or async) context manager:

        guard = RateGuard()
        with guard:
            client.get_account()
        async with guard:
            await client.get_account_async()

    On entry, blocks until a token is available. Tokens regenerate at
    `max_per_min / 60` per second.
    """

    def __init__(self, *, max_per_min: int = ALPACA_MAX_CALLS_PER_MIN,
                  source: str = "alpaca"):
        self.max_per_min = max_per_min
        self.source = source
        self._timestamps: deque[float] = deque(maxlen=max_per_min + 50)
        self._lock = threading.Lock()
        self._block_count = 0

    def can_proceed(self) -> tuple[bool, float]:
        """Return (allowed, sleep_seconds_if_denied).

        Pure check — does not consume a token. Used by alpaca_ws_patch
        to decide whether to sleep before retrying."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) < self.max_per_min:
                return True, 0.0
            # Sleep until the oldest call ages out
            oldest = self._timestamps[0]
            sleep_for = max(0.0, (oldest + 60.0) - now)
            return False, sleep_for

    def consume(self) -> None:
        """Record one call. Does not block — assumes caller already
        checked can_proceed or is willing to overshoot."""
        with self._lock:
            self._timestamps.append(time.monotonic())

    def block_until_allowed(self, *, timeout_sec: float = 60.0) -> bool:
        """Block until budget allows. Returns True if proceeded, False
        if timeout."""
        deadline = time.monotonic() + timeout_sec
        while True:
            ok, sleep_for = self.can_proceed()
            if ok:
                self.consume()
                return True
            if time.monotonic() + sleep_for > deadline:
                self._block_count += 1
                log.warning("RateGuard timeout: %s budget exhausted",
                             self.source)
                return False
            time.sleep(min(sleep_for, 1.0))

    async def async_block_until_allowed(self, *,
                                          timeout_sec: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout_sec
        while True:
            ok, sleep_for = self.can_proceed()
            if ok:
                self.consume()
                return True
            if time.monotonic() + sleep_for > deadline:
                self._block_count += 1
                return False
            await asyncio.sleep(min(sleep_for, 1.0))

    def __enter__(self):
        self.block_until_allowed()
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    async def __aenter__(self):
        await self.async_block_until_allowed()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    @property
    def current_rate_per_min(self) -> int:
        """How many calls in the last 60 seconds (post-cleanup)."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)

    @property
    def block_count(self) -> int:
        return self._block_count


# ─── Module-global guard instance for the entire alpaca-py SDK ─────────────
_GLOBAL_GUARD: RateGuard | None = None


def get_global_guard() -> RateGuard:
    global _GLOBAL_GUARD
    if _GLOBAL_GUARD is None:
        _GLOBAL_GUARD = RateGuard(
            max_per_min=ALPACA_MAX_CALLS_PER_MIN, source="alpaca")
    return _GLOBAL_GUARD


# ─── Stall probe ───────────────────────────────────────────────────────────


async def probe_ws_slot_free(*, api_key: str, api_secret: str,
                               feed_name: str = "iex",
                               timeout_sec: float = 10.0) -> tuple[bool, str]:
    """Cheap probe to detect when Alpaca's data-WS slot is free.

    Performs the minimal _connect + _auth flow against
    wss://stream.data.alpaca.markets/v2/<feed_name>. Returns
    (free, detail). When `free=True` the caller may proceed to
    establish the real WebSocket with subscriptions.

    Cost: 1 Alpaca request (auth). Probe loop at 5-sec interval =
    12/min — well under the 200/min global budget.
    """
    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
        feed = DataFeed.IEX if feed_name.lower() == "iex" else DataFeed.SIP
        ws = StockDataStream(api_key, api_secret, feed=feed)
        try:
            await asyncio.wait_for(ws._connect(), timeout=timeout_sec)
            await asyncio.wait_for(ws._auth(), timeout=timeout_sec)
            try:
                await ws.close()
            except Exception:
                pass
            return True, "auth ok"
        except Exception as e:
            try:
                await ws.close()
            except Exception:
                pass
            return False, f"{type(e).__name__}: {str(e)[:120]}"
    except Exception as e:
        return False, f"setup_error: {type(e).__name__}: {str(e)[:80]}"


async def wait_until_ws_slot_free(*, api_key: str, api_secret: str,
                                     max_wait_sec: int = 600,
                                     interval_sec: int = ALPACA_STALL_PROBE_INTERVAL_SEC
                                     ) -> tuple[bool, int, str]:
    """Probe the WS slot every `interval_sec` until free or `max_wait_sec`
    elapses. Returns (succeeded, attempts, last_detail).

    Use this when alpaca_ws_patch detects a stall — instead of sleeping
    blindly for 30-60s, poll cheaply at 5s and proceed the INSTANT the
    server releases the slot. Worst case still <= 60s of probes per min,
    so the rate budget is preserved.
    """
    deadline = time.monotonic() + max_wait_sec
    attempts = 0
    last_detail = ""
    while time.monotonic() < deadline:
        attempts += 1
        free, detail = await probe_ws_slot_free(
            api_key=api_key, api_secret=api_secret)
        last_detail = detail
        if free:
            log.info("WS slot free after %d probes (%s)",
                      attempts, detail)
            return True, attempts, detail
        log.debug("WS still locked [probe %d]: %s", attempts, detail)
        await asyncio.sleep(interval_sec)
    log.warning("WS slot NEVER freed after %d probes (timeout %ds): %s",
                 attempts, max_wait_sec, last_detail)
    return False, attempts, last_detail
