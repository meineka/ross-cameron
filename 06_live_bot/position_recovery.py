"""Position-State-Recovery: bei Bot-Crash/Cloud-Restart mit offenen Positions
sicher übernehmen statt blind weitertraden.

Default-Policy: ALLE Positions sofort schließen (close_all_positions). Sicher,
da Bot-internen Stops/Targets nach Crash unbekannt sind.

Audit-Iter 6 (2026-05-12) — robustness fixes:
  PR-1: Single-shot close ohne retry → 3 Attempts + Polling bis flat
  PR-2: Return-Value spiegelt jetzt Erfolg wider (count wenn flat, -1 wenn nicht)
  PR-6: Async-Fill-Verification via Poll bis positions==0 oder Timeout

Returns:
   0  = clean start, keine Positionen
  -1  = recovery FAILED (API down ODER positions blieben offen — bot MUSS abort)
  >0  = N Positionen wurden erkannt UND erfolgreich geflattened
"""
from __future__ import annotations
import logging
import time

log = logging.getLogger("recovery")


def recover_or_flatten(trading_client, *,
                       mode: str = "flatten",
                       max_attempts: int = 3,
                       verify_timeout_sec: float = 30.0,
                       poll_interval_sec: float = 1.5) -> int:
    """Returns:
       0 = bereits flat, kein Eingriff nötig
      -1 = recovery FAILED (caller muss bot stoppen!)
      >0 = N Positions erfolgreich geflattened
    """
    def _list():
        try:
            return list(trading_client.get_all_positions() or [])
        except Exception as e:
            log.error("position-recovery: get_all_positions failed: %s", e)
            return None

    initial = _list()
    if initial is None:
        log.error("position-recovery: kann Position-Liste nicht laden → CRITICAL")
        return -1
    if not initial:
        log.info("position-recovery: 0 open positions — clean start")
        return 0

    log.warning("position-recovery: %d Positions gefunden — mode=%s",
                len(initial), mode)
    for p in initial:
        log.warning("  %s qty=%s avg_entry=%s",
                    getattr(p, "symbol", "?"),
                    getattr(p, "qty", "?"),
                    getattr(p, "avg_entry_price", "?"))

    if mode == "report-only":
        return len(initial)

    if mode != "flatten":
        log.error("position-recovery: unknown mode=%r — abort", mode)
        return -1

    n_initial = len(initial)
    for attempt in range(1, max_attempts + 1):
        try:
            trading_client.close_all_positions(cancel_orders=True)
            log.warning("position-recovery: close_all submitted (attempt %d/%d)",
                        attempt, max_attempts)
        except Exception as e:
            log.error("position-recovery: close_all err (attempt %d/%d): %s",
                      attempt, max_attempts, e)
        deadline = time.time() + verify_timeout_sec
        while time.time() < deadline:
            cur = _list()
            if cur == []:
                log.warning("position-recovery: FLAT after attempt %d", attempt)
                return n_initial
            time.sleep(poll_interval_sec)
        log.warning("position-recovery: positions remain after attempt %d", attempt)

    final = _list()
    if final == []:
        return n_initial
    log.error("position-recovery: CRITICAL — positions NOT flattened: %s",
              [getattr(p, "symbol", "?") for p in (final or [])])
    return -1
