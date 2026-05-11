"""Float-Filter — 5. Cameron-Pillar: Free Float < 10 M Shares.

Best-Effort: yfinance .info["floatShares"]. Bei Fehler oder None → True
(kein Veto, lass durch). Cache pro Symbol pro Tag.
"""
from __future__ import annotations
import logging

log = logging.getLogger("float")

MAX_FLOAT = 10_000_000
_cache: dict[str, float | None] = {}


def get_float(symbol: str) -> float | None:
    if symbol in _cache:
        return _cache[symbol]
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        v = info.get("floatShares")
        _cache[symbol] = float(v) if v else None
    except Exception as e:
        log.debug("float-fetch %s: %s", symbol, e)
        _cache[symbol] = None
    return _cache[symbol]


def passes_float_filter(symbol: str, max_float: float = MAX_FLOAT) -> bool:
    f = get_float(symbol)
    if f is None:
        return True  # unknown → don't veto
    return f <= max_float
