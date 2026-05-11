"""Exponential-Backoff + Circuit-Breaker für WS-Reconnects.

2026-05-11-Lesson: 14× reconnect in <2 min → wäre fast in Alpaca-Rate-Limit
gelaufen. Jetzt: 1s, 2s, 4s, 8s, 16s, 32s (cap 60s). Nach N consecutive Fails
→ Circuit-Breaker, kein weiterer Retry, ALARM für Audit.
"""
from __future__ import annotations
import asyncio
import logging

log = logging.getLogger("ws-backoff")


class ReconnectBackoff:
    def __init__(self, base_sec: float = 1.0, cap_sec: float = 60.0, max_consec_fails: int = 8):
        self.base = base_sec
        self.cap = cap_sec
        self.max_consec_fails = max_consec_fails
        self.consec_fails = 0

    def reset(self) -> None:
        self.consec_fails = 0

    def fail(self) -> float:
        """Returns next sleep duration. Raises RuntimeError if circuit breaker trips."""
        self.consec_fails += 1
        if self.consec_fails > self.max_consec_fails:
            raise RuntimeError(
                f"WS-Circuit-Breaker tripped: {self.consec_fails} consecutive fails — "
                "needs human/code-fix, not blind reconnect"
            )
        delay = min(self.cap, self.base * (2 ** (self.consec_fails - 1)))
        return delay

    async def sleep_after_fail(self) -> None:
        delay = self.fail()
        log.warning("Backoff: sleep %.1fs (fail #%d/%d)", delay,
                    self.consec_fails, self.max_consec_fails)
        await asyncio.sleep(delay)
