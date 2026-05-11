"""Position-State-Recovery: bei Bot-Crash mit offenen Positions sicher
übernehmen statt blind weitertraden.

Default-Policy: ALLE Positions sofort schließen (close_all_positions). Sicher,
da Bot-internen Stops/Targets nach Crash unbekannt sind. Bessere Variante
(state-file) kann später eingebaut werden.
"""
from __future__ import annotations
import logging

log = logging.getLogger("recovery")


def recover_or_flatten(trading_client, *, mode: str = "flatten") -> int:
    """Returns Anzahl der Positions die behandelt wurden."""
    try:
        positions = trading_client.get_all_positions()
    except Exception as e:
        log.error("position-recovery: Alpaca-fetch failed: %s", e)
        return -1
    if not positions:
        log.info("position-recovery: 0 open positions — clean start")
        return 0
    log.warning("position-recovery: %d Positions gefunden — mode=%s", len(positions), mode)
    for p in positions:
        log.warning("  %s qty=%s avg_entry=%s", p.symbol, p.qty, p.avg_entry_price)
    if mode == "flatten":
        try:
            trading_client.close_all_positions(cancel_orders=True)
            log.warning("position-recovery: alle Positions geschlossen (cancel_orders=True)")
        except Exception as e:
            log.error("close_all_positions failed: %s", e)
    return len(positions)
