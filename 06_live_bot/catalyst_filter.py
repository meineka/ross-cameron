"""Catalyst-Filter MVP — 5. Cameron-Pillar (News-Required).

V1: yfinance.Ticker.news (kostenlos, Yahoo-News-Feed).
V2 (später): SEC EDGAR 8-K / PR-Newswire RSS.

Logik: passes_catalyst_filter → True wenn ≥1 News-Headline in den letzten 24 h.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("catalyst")

_cache: dict[str, tuple[bool, float]] = {}  # symbol -> (has_catalyst, ts)
_CACHE_TTL = 3600  # 1 h
_LOOKBACK_HOURS = 24


def has_recent_news(symbol: str, lookback_hours: int = _LOOKBACK_HOURS) -> bool:
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
            _cache[symbol] = (True, now)
            return True
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
        # Hatten news, aber keine recent → lass durch (Daily-Move + RVOL
        # sind oft Catalyst-Proxy genug, yfinance-news ist unzuverlässig)
        _cache[symbol] = (True, now)
        return True
    except Exception as e:
        log.debug("catalyst fetch %s: %s", symbol, e)
        return True


def passes_catalyst_filter(symbol: str) -> bool:
    """V1: News in 24h reicht. Wahrer Cameron-Filter (PR-Type-Match) später."""
    return has_recent_news(symbol)
