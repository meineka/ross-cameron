"""Persistenter Delisted-Cache.

Heute (12.05.2026) erzeugten ~3000 delisted Tickers tausende ERROR-Logs
und ALARMs im Audit-Monitor. Lösung: bekannte Tote per Cache filtern
bevor sie yfinance erreichen.

- Format: JSON-Set mit ticker-Symbolen + last_seen-Date
- Aging: nach 30 Tagen Re-Verify (manche Tickers kommen zurück via Reverse-Split etc.)
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from datetime import datetime, timedelta

CACHE_FILE = Path(__file__).parent / "delisted_cache.json"
TTL_DAYS = 30

_cache: dict[str, float] | None = None


def _load() -> dict[str, float]:
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return _cache
        except Exception:
            pass
    _cache = {}
    return _cache


def _save() -> None:
    if _cache is None:
        return
    try:
        CACHE_FILE.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def is_delisted(symbol: str) -> bool:
    """True wenn symbol in den letzten TTL_DAYS als delisted geflaggt wurde."""
    c = _load()
    if symbol not in c:
        return False
    age_days = (time.time() - c[symbol]) / 86400
    if age_days > TTL_DAYS:
        del c[symbol]
        _save()
        return False
    return True


def mark_delisted(symbol: str) -> None:
    c = _load()
    c[symbol] = time.time()
    _save()


def filter_known_delisted(tickers: list[str]) -> tuple[list[str], int]:
    """Returns (alive_tickers, skipped_count)."""
    c = _load()
    now = time.time()
    cutoff = now - TTL_DAYS * 86400
    alive = []
    skipped = 0
    for t in tickers:
        ts = c.get(t)
        if ts and ts >= cutoff:
            skipped += 1
        else:
            alive.append(t)
    return alive, skipped


def mark_batch_delisted(symbols: list[str]) -> None:
    if not symbols:
        return
    c = _load()
    now = time.time()
    for s in symbols:
        c[s] = now
    _save()


def stats() -> dict:
    c = _load()
    return {"total_cached": len(c), "cache_file": str(CACHE_FILE)}
