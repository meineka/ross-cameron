"""Persistenter Delisted-Cache.

Heute (12.05.2026) erzeugten ~3000 delisted Tickers tausende ERROR-Logs
und ALARMs im Audit-Monitor. Lösung: bekannte Tote per Cache filtern
bevor sie yfinance erreichen.

- Format: JSON-Set mit ticker-Symbolen + last_seen-Date
- Aging: nach 30 Tagen Re-Verify (manche Tickers kommen zurück via Reverse-Split etc.)

Audit-Iter 23 (2026-05-12) — Bug-Fixes DC-1/DC-3/DC-6:
  DC-1: atomic write via tmp+rename — Crash mid-write hätte sonst
        ganze Delisted-History gewipt (kaputtes JSON → load failed
        → _cache={} → 3000 dead-tickers wieder live).
  DC-3: corrupt-JSON loggt warning statt silent-reset.
  DC-6: defensive `ts is not None` Check (ts=0.0 wäre falsy gewesen).
"""
from __future__ import annotations
import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("delisted_cache")

CACHE_FILE = Path(__file__).parent / "delisted_cache.json"
TTL_DAYS = 30

_cache: dict[str, float] | None = None
_lock = threading.Lock()


def _load() -> dict[str, float]:
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if not isinstance(_cache, dict):
                log.warning("delisted_cache: unexpected format (not dict) — reset")
                _cache = {}
            return _cache
        except json.JSONDecodeError as e:
            log.warning("delisted_cache: corrupt JSON, reset cache: %s", e)
        except OSError as e:
            log.warning("delisted_cache: read failed: %s", e)
    _cache = {}
    return _cache


def _save() -> None:
    """Audit-Iter 23 (Bug DC-1): atomic write via tmp+rename."""
    if _cache is None:
        return
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    try:
        # Write to tmp file first
        tmp.write_text(json.dumps(_cache, indent=2), encoding="utf-8")
        # Atomic rename (POSIX); auf Windows seit Python 3.8 auch atomic
        os.replace(str(tmp), str(CACHE_FILE))
    except OSError as e:
        log.warning("delisted_cache: save failed: %s", e)
        # Cleanup tmp falls existent
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def reset_cache() -> None:
    """Für Tests: cache leeren ohne file zu touchen."""
    global _cache
    _cache = None


def is_delisted(symbol: str) -> bool:
    """True wenn symbol in den letzten TTL_DAYS als delisted geflaggt wurde."""
    with _lock:
        c = _load()
        if symbol not in c:
            return False
        ts = c[symbol]
        # Audit-Iter 23 (Bug DC-6): defensive None+type check
        if ts is None or not isinstance(ts, (int, float)):
            del c[symbol]
            _save()
            return False
        age_days = (time.time() - ts) / 86400
        if age_days > TTL_DAYS:
            del c[symbol]
            _save()
            return False
        return True


def mark_delisted(symbol: str) -> None:
    with _lock:
        c = _load()
        c[symbol] = time.time()
        _save()


def filter_known_delisted(tickers: list[str]) -> tuple[list[str], int]:
    """Returns (alive_tickers, skipped_count)."""
    with _lock:
        c = _load()
    now = time.time()
    cutoff = now - TTL_DAYS * 86400
    alive = []
    skipped = 0
    for t in tickers:
        ts = c.get(t)
        # Audit-Iter 23 (Bug DC-6): ts is not None statt truthy
        if ts is not None and isinstance(ts, (int, float)) and ts >= cutoff:
            skipped += 1
        else:
            alive.append(t)
    return alive, skipped


def mark_batch_delisted(symbols: list[str]) -> None:
    if not symbols:
        return
    with _lock:
        c = _load()
        now = time.time()
        for s in symbols:
            c[s] = now
        _save()


def stats() -> dict:
    with _lock:
        c = _load()
        now = time.time()
        cutoff = now - TTL_DAYS * 86400
        live_count = sum(1 for v in c.values()
                         if isinstance(v, (int, float)) and v >= cutoff)
    return {
        "total_cached": len(c),
        "live_count": live_count,
        "expired_count": len(c) - live_count,
        "cache_file": str(CACHE_FILE),
        "ttl_days": TTL_DAYS,
    }
