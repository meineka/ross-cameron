"""Exponential-Backoff + Circuit-Breaker für WS-Reconnects.

2026-05-11-Lesson: 14× reconnect in <2 min → wäre fast in Alpaca-Rate-Limit
gelaufen. Jetzt: 1s, 2s, 4s, 8s, 16s, 32s (cap 60s). Nach N consecutive Fails
→ Circuit-Breaker, kein weiterer Retry, ALARM für Audit.

Audit-Iter 24 (2026-05-12) — Bug-Fixes RB-7/RB-9:
  RB-7: defensive input validation (negative base/cap → ValueError)
  RB-9: optional jitter (default off für deterministic tests)
"""
from __future__ import annotations
import asyncio
import logging
import random

log = logging.getLogger("ws-backoff")


class ReconnectBackoff:
    def __init__(self, base_sec: float = 1.0, cap_sec: float = 60.0,
                 max_consec_fails: int = 8, jitter: float = 0.0):
        """Audit-Iter 24:
          - input validation für pathological configs
          - jitter: 0.0 = deterministic (default), 0.1 = ±10% Random für
            thundering-herd-Schutz wenn mehrere Bots gleichzeitig reconnecten.
        """
        if base_sec <= 0:
            raise ValueError(f"base_sec must be > 0, got {base_sec}")
        if cap_sec < base_sec:
            raise ValueError(f"cap_sec ({cap_sec}) must be >= base_sec ({base_sec})")
        if max_consec_fails < 0:
            raise ValueError(f"max_consec_fails must be >= 0, got {max_consec_fails}")
        if jitter < 0 or jitter > 1:
            raise ValueError(f"jitter must be in [0, 1], got {jitter}")
        self.base = base_sec
        self.cap = cap_sec
        self.max_consec_fails = max_consec_fails
        self.jitter = jitter
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
        # Optional Jitter (RB-9): vermeidet thundering-herd wenn mehrere
        # Reconnect-Clients gleichzeitig denselben Backoff-Wert haben.
        if self.jitter > 0:
            delay *= (1 + random.uniform(-self.jitter, self.jitter))
        return max(0.0, delay)

    async def sleep_after_fail(self) -> None:
        delay = self.fail()
        log.warning("Backoff: sleep %.1fs (fail #%d/%d)", delay,
                    self.consec_fails, self.max_consec_fails)
        await asyncio.sleep(delay)
