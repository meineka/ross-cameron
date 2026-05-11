"""VWAP-Reclaim-Filter: Cameron tradet NUR über VWAP.

Bot bekommt 5-Min-Bars per WS. Session-VWAP = cumulative typical_price * vol /
cumulative vol.
"""
from __future__ import annotations
from typing import Iterable


def session_vwap(bars: Iterable[dict]) -> float | None:
    """bars: dicts mit 'high','low','close','volume'. Returns session-VWAP."""
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars:
        tp = (b["high"] + b["low"] + b["close"]) / 3.0
        v = b["volume"]
        cum_pv += tp * v
        cum_v += v
    if cum_v <= 0:
        return None
    return cum_pv / cum_v


def is_above_vwap(bars: list[dict], current_close: float) -> bool:
    v = session_vwap(bars)
    if v is None:
        return True  # ohne Daten kein Veto
    return current_close > v
