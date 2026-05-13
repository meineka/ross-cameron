"""Float-Filter — 5. Cameron-Pillar: Free Float < 10 M Shares.

Best-Effort: yfinance .info["floatShares"]. Bei Fehler oder None → True
(kein Veto, lass durch).

Audit-Iter 10 (2026-05-12) — Bug-Fixes FLT-4/FLT-1:
  FLT-4: Cache hatte keine TTL → in 24h+-daemon-Runs blieben gestrige
         Floats hängen. Float kann ändern (Secondary, Unlock). Jetzt
         TTL 12h, plus None-Werte werden nur 5min gecached (retry).
  FLT-1: Module-level _cache war für Tests nicht isoliert → clear_cache().
"""
from __future__ import annotations
import logging
import time

log = logging.getLogger("float")

MAX_FLOAT = 10_000_000
_TTL_KNOWN_SEC = 12 * 3600       # 12h für gültige Werte
_TTL_UNKNOWN_SEC = 300            # 5min für None (yfinance-Glitch retry)
_cache: dict[str, tuple[float | None, float]] = {}  # symbol -> (val, ts)


def clear_cache() -> None:
    """Für Tests/Daemon-Restart. Auch von premarket-Scan callable um
    Daily-Refresh sicherzustellen."""
    _cache.clear()


def get_float(symbol: str) -> float | None:
    now = time.time()
    if symbol in _cache:
        val, ts = _cache[symbol]
        ttl = _TTL_KNOWN_SEC if val is not None else _TTL_UNKNOWN_SEC
        if now - ts < ttl:
            return val
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).info
        v = info.get("floatShares")
        # 0 oder None → unknown; positive float → known
        parsed: float | None = float(v) if v and v > 0 else None
        _cache[symbol] = (parsed, now)
    except Exception as e:
        log.debug("float-fetch %s: %s", symbol, e)
        _cache[symbol] = (None, now)
    return _cache[symbol][0]


def passes_float_filter(symbol: str, max_float: float = MAX_FLOAT) -> bool:
    f = get_float(symbol)
    if f is None:
        return True  # unknown → don't veto
    return f <= max_float
