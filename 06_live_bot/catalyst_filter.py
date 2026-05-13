"""Catalyst-Filter MVP — 5. Cameron-Pillar (News-Required).

V1: yfinance.Ticker.news (kostenlos, Yahoo-News-Feed).
V2 (später): SEC EDGAR 8-K / PR-Newswire RSS.

⚠️  V1-Behavior: Diese Funktion kann aktuell NIE False returnen — yfinance-News
ist unzuverlässig genug dass wir "don't veto on tooling fail" angenommen
haben. Daily-Move + RVOL sind als Catalyst-Proxy gewählt. Wenn das ändern
soll, V2 mit SEC-EDGAR/PR-Newswire bauen + strict_mode-Param hier
einbauen.

Audit-Iter 10 (2026-05-12) — Bug-Fix CAT-1:
  - clear_cache() für Tests/Daily-Reset
  - strict=True optional: bei API-Failure False statt True (für
    Live-Setups die wirklich nur mit bestätigtem Catalyst traden wollen)
"""
from __future__ import annotations
import logging
import time
from datetime import datetime

log = logging.getLogger("catalyst")

_cache: dict[str, tuple[bool, float]] = {}  # symbol -> (has_catalyst, ts)
_CACHE_TTL = 3600  # 1 h
_LOOKBACK_HOURS = 24


def clear_cache() -> None:
    """Für Tests + Daily-Reset im Premarket-Scan."""
    _cache.clear()


def has_recent_news(symbol: str, lookback_hours: int = _LOOKBACK_HOURS,
                    strict: bool = False) -> bool:
    """strict=True: bei API-Failure False statt True. Default False für
    V1-Permissive."""
    now = time.time()
    if symbol in _cache:
        val, ts = _cache[symbol]
        if now - ts < _CACHE_TTL:
            return val
    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
        # yfinance unzuverlässig: bei empty list NICHT veto-en (Cameron's
        # Filter darf nicht an unserer Data-Source scheitern)
        if not news:
            result = False if strict else True
            _cache[symbol] = (result, now)
            return result
        cutoff = now - lookback_hours * 3600
        for n in news:
            ts_pub = n.get("providerPublishTime") or n.get("pubDate") or 0
            if isinstance(ts_pub, str):
                try:
                    ts_pub = datetime.fromisoformat(ts_pub.replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
            if ts_pub >= cutoff:
                _cache[symbol] = (True, now)
                return True
        # Hatten news, aber keine recent → V1: lass durch. Strict: veto.
        result = False if strict else True
        _cache[symbol] = (result, now)
        return result
    except Exception as e:
        log.debug("catalyst fetch %s: %s", symbol, e)
        # Bei Exception NICHT cachen — nächster Call retry
        return False if strict else True


def passes_catalyst_filter(symbol: str, strict: bool = False) -> bool:
    """V1: News in 24h reicht. Wahrer Cameron-Filter (PR-Type-Match) später."""
    return has_recent_news(symbol, strict=strict)
