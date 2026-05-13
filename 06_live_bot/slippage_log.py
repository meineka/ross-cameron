"""Slippage-Telemetrie: jede Order's expected vs filled.

Wenn drift > 0.5 % → ALERT. Für Post-Mortem: was kostet uns die Strategie wirklich.

Audit-Iter 17 (2026-05-12):
  LOG-5: drift_pct returnt 0.0 wenn expected ist 0 ODER negativ —
         vorher silent-skip via falsy. Negative-price ist data-error,
         jetzt explizit unhandled mit "drift_unknown" flag.
  LOG-2: threading.Lock auch hier (concurrent fills möglich)
  LOG-1/3: flush+fsync + try/except defensive write
"""
from __future__ import annotations
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("slippage")
SLIP_FILE = Path(__file__).parent / "slippage.jsonl"
ALERT_DRIFT_PCT = 0.5
_lock = threading.Lock()


def record_fill(symbol: str, side: str, qty: int, expected: float, filled: float) -> dict:
    drift_abs = filled - expected
    if expected > 0:
        drift_pct = (drift_abs / expected) * 100
        drift_known = True
    else:
        # Audit-Iter 17 (Bug LOG-5): expected <= 0 ist data-error,
        # statt silent 0.0 → flag setzen damit Post-Mortem das erkennt
        drift_pct = 0.0
        drift_known = False
    entry = {
        "ts": datetime.now().isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "expected": round(expected, 4),
        "filled": round(filled, 4),
        "drift_abs": round(drift_abs, 4),
        "drift_pct": round(drift_pct, 3),
        "drift_known": drift_known,
    }
    try:
        line = json.dumps(entry) + "\n"
    except (TypeError, ValueError) as e:
        log.warning("slippage serialize failed: %s", e)
        return entry
    try:
        with _lock:
            with SLIP_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
    except (OSError, IOError) as e:
        log.warning("slippage log write failed: %s", e)
    if drift_known and abs(drift_pct) > ALERT_DRIFT_PCT:
        log.warning(
            "SLIPPAGE-ALERT %s %s %d: expected=%.4f filled=%.4f drift=%+.2f%%",
            side, symbol, qty, expected, filled, drift_pct,
        )
    elif not drift_known:
        log.warning(
            "SLIPPAGE-DATA-ERR %s %s %d: expected=%.4f filled=%.4f (no drift calc)",
            side, symbol, qty, expected, filled,
        )
    return entry
