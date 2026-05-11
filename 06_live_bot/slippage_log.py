"""Slippage-Telemetrie: jede Order's expected vs filled.

Wenn drift > 0.5 % → ALERT. Für Post-Mortem: was kostet uns die Strategie wirklich.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("slippage")
SLIP_FILE = Path(__file__).parent / "slippage.jsonl"
ALERT_DRIFT_PCT = 0.5


def record_fill(symbol: str, side: str, qty: int, expected: float, filled: float) -> dict:
    drift_abs = filled - expected
    drift_pct = (drift_abs / expected) * 100 if expected else 0.0
    entry = {
        "ts": datetime.now().isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "expected": round(expected, 4),
        "filled": round(filled, 4),
        "drift_abs": round(drift_abs, 4),
        "drift_pct": round(drift_pct, 3),
    }
    try:
        with SLIP_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.warning("slippage log write failed: %s", e)
    if abs(drift_pct) > ALERT_DRIFT_PCT:
        log.warning(
            "SLIPPAGE-ALERT %s %s %d: expected=%.4f filled=%.4f drift=%+.2f%%",
            side, symbol, qty, expected, filled, drift_pct,
        )
    return entry
