"""VWAP-Reclaim-Filter: Cameron tradet NUR über VWAP.

Bot bekommt 5-Min-Bars per WS. Session-VWAP = cumulative typical_price * vol /
cumulative vol.

Audit-Iter 21 (2026-05-12) — Bug-Fixes VWAP-2/VWAP-4:
  - Defensive bar-validation: KeyError/TypeError → skip bar statt crash
  - Negative volume bars werden geskipt (data-corruption)
  - strict=True optional in is_above_vwap: bei no-data False (veto)
"""
from __future__ import annotations
from typing import Iterable


def session_vwap(bars: Iterable[dict]) -> float | None:
    """bars: dicts mit 'high','low','close','volume'. Returns session-VWAP.

    Skipt defensiv Bars mit fehlenden Keys, None-Werten, oder
    negative-volume (Daten-Anomalie). Returns None wenn keine
    valid Bars vorhanden sind.
    """
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars:
        try:
            h = float(b["high"])
            l = float(b["low"])
            c = float(b["close"])
            v = float(b["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        # Audit-Iter 21 (Bug VWAP-4): negative volume = data error → skip
        if v < 0:
            continue
        # NaN-Check via != self
        if h != h or l != l or c != c or v != v:
            continue
        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_v += v
    if cum_v <= 0:
        return None
    return cum_pv / cum_v


def is_above_vwap(bars: list[dict], current_close: float,
                  strict: bool = False) -> bool:
    """strict=True: bei VWAP=None False statt True returnen — für
    risiko-averse Setups die wirklich nur über VWAP traden wollen."""
    v = session_vwap(bars)
    if v is None:
        return False if strict else True
    return current_close > v
