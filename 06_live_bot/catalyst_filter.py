"""Catalyst-Filter — 5. Cameron-Pillar (News-Required).

Data source: yfinance.Ticker.news (free, Yahoo-Feed). V2 (later): SEC
EDGAR 8-K / PR-Newswire RSS for stricter validation.

Review-V2 P1.3 fix (2026-05-14):
==================================
Previous "strict=True" was permissive on empty/error (data-source-trust).
Reviewer correctly identified that as wrong: if catalyst is REQUIRED for
trading, unknown catalyst must mean NO trade. Otherwise "required" is a
lie.

NEW semantic — three explicit modes:
  - "off":    never block (filter disabled entirely)
  - "soft":   only block on stale news (news returned but all old). Empty
              and errors PASS with warning. This is the old behavior under
              `strict=False`.
  - "strict": block on ANY unknown — empty news, API exception, or all-stale.
              "I don't know if there's a catalyst" → "I won't trade".
              This is what CATALYST_REQUIRED=True should give a live trader.

Legacy `strict=False` → mode="soft", `strict=True` → mode="strict".
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


def _resolve_mode(strict: bool | None, mode: str | None) -> str:
    """Translate legacy strict-bool to mode-string."""
    if mode is not None:
        if mode not in ("off", "soft", "strict"):
            raise ValueError(f"catalyst mode must be off|soft|strict, got {mode!r}")
        return mode
    if strict is True:
        return "strict"
    return "soft"  # legacy default (V1 permissive)


def has_recent_news(symbol: str, lookback_hours: int = _LOOKBACK_HOURS,
                    strict: bool | None = None, mode: str | None = None) -> bool:
    """Returns True if symbol has fresh news (or in soft-mode on unknown).

    mode="off" → always True (filter disabled).
    mode="soft" → True on empty/error, False only when news exists but all stale.
    mode="strict" → False on empty/error/all-stale (fail-closed).

    Legacy: strict=False maps to "soft", strict=True maps to "strict".
    """
    resolved = _resolve_mode(strict, mode)
    if resolved == "off":
        return True

    now = time.time()
    if symbol in _cache:
        val, ts = _cache[symbol]
        if now - ts < _CACHE_TTL:
            return val
    try:
        import yfinance as yf
        news = yf.Ticker(symbol).news or []
        if not news:
            # Empty news feed. Soft-mode: treat as data-source-quirk, pass.
            # Strict-mode: treat as "unknown catalyst", block.
            if resolved == "strict":
                # Don't cache the negative — yfinance may recover next call.
                log.info("catalyst STRICT-block %s: empty news feed", symbol)
                return False
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
        # News exist but all older than lookback → genuine "no fresh catalyst".
        # BOTH soft and strict block here. (Soft only passes empty/error.)
        log.info("catalyst block %s: news exist but none in last %dh", symbol, lookback_hours)
        _cache[symbol] = (False, now)
        return False
    except Exception as e:
        log.debug("catalyst fetch %s: %s", symbol, e)
        # API-error: strict blocks (don't know = don't trade), soft passes.
        if resolved == "strict":
            # Don't cache — retry on next call
            log.warning("catalyst STRICT-block %s: yfinance error %s", symbol, e)
            return False
        return True


def passes_catalyst_filter(symbol: str, strict: bool | None = None,
                           mode: str | None = None) -> bool:
    """Cameron Pillar-5 news-required filter.

    Legacy strict-bool API preserved. Recommended: use mode="strict" for
    live trading with CATALYST_REQUIRED=True, mode="soft" for paper, "off"
    to disable.
    """
    return has_recent_news(symbol, strict=strict, mode=mode)
