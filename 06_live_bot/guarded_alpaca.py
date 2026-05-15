"""guarded_alpaca.py — Phase-53 (2026-05-15)

User-driven response to ChatGPT review feedback (5 consecutive answer
files, P0): "RateGuard exists but is NOT WIRED into the live Alpaca
SDK call sites. Bot makes raw TradingClient.get_account() /
submit_order() / get_order_by_id() / get_stock_snapshot() /
get_stock_bars() calls without going through the guard."

ChatGPT-demanded API:
  - GuardedTradingClient            (wraps TradingClient)
  - GuardedStockHistoricalDataClient (wraps StockHistoricalDataClient)
  - alpaca_api_calls.jsonl           (per-call log with blocked_ms)
  - rate_per_min                     (live metric for status_dashboard)

This module enforces the documented Alpaca 200 req/min cap
PROCESS-WIDE via a single shared RateGuard. Every guarded call:
  1. blocks until a token is free (logs `blocked_ms` if it had to wait)
  2. measures call latency
  3. writes one JSONL row with method, source, status, error_class
  4. propagates the original return value (or re-raises errors)

Drop-in replacement: caller writes
    from guarded_alpaca import GuardedTradingClient
    tc = GuardedTradingClient(key, secret, paper=True)
    tc.get_account()                # same API as TradingClient
"""
from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger("guarded-alpaca")

HERE = Path(__file__).resolve().parent
ALPACA_API_CALLS_LOG = HERE / "alpaca_api_calls.jsonl"

# Default JSONL appender — shares one file lock for all guarded clients
_log_lock = Lock()


def _log_call(*, source: str, method: str, status: str,
              latency_ms: float, blocked_ms: float,
              error_class: str | None = None,
              extra: dict | None = None) -> None:
    """Append one row to alpaca_api_calls.jsonl. Schema is stable so
    operators / dashboards can grep + aggregate."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "source": source,           # "alpaca-trading" | "alpaca-data"
        "method": method,           # e.g. "get_account", "submit_order"
        "status": status,           # "ok" | "error"
        "latency_ms": round(latency_ms, 2),
        "blocked_ms": round(blocked_ms, 2),
        "error_class": error_class,
        "extra": extra or {},
    }
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    try:
        with _log_lock:
            with open(ALPACA_API_CALLS_LOG, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        # never crash the live trading thread because logging failed
        log.debug("alpaca-api-call log write failed: %s", e)


class AlpacaRateLimitBlocked(Exception):
    """Phase-55: raised when the global RateGuard cannot grant a token
    within the configured timeout. Distinct from network/auth errors so
    callers can decide policy (retry, drop, escalate alert)."""


# Phase-55 (2026-05-15, ChatGPT P0): timeout policy for block_until_allowed.
# When budget is exhausted and waiting times out, the guard returns False
# and we MUST refuse the call — not silently bypass like the previous
# fail-open behavior. The actual wait timeout is parameterised so
# market-data calls can choose shorter waits than order calls if they want.
DEFAULT_BLOCK_TIMEOUT_SEC = 30.0


def _guarded_invoke(*, guard, source: str, method_name: str,
                     callable_fn, args, kwargs,
                     block_timeout_sec: float = DEFAULT_BLOCK_TIMEOUT_SEC):
    """Single chokepoint: rate-block, time, log, propagate.

    Phase-55 (fail-closed): if the rate-guard denies access within
    `block_timeout_sec`, the wrapped Alpaca call is NOT executed.
    Instead we log `status="blocked"` and raise AlpacaRateLimitBlocked.
    """
    t_block_start = time.monotonic()
    allowed = guard.block_until_allowed(timeout_sec=block_timeout_sec)
    blocked_ms = (time.monotonic() - t_block_start) * 1000
    # current_rate_per_min may be: (a) int property on RateGuard,
    # (b) callable function, (c) MagicMock in tests. Resolve safely.
    raw = getattr(guard, "current_rate_per_min", 0)
    try:
        if callable(raw):
            raw = raw()
        rate_now = int(raw) if isinstance(raw, (int, float)) else 0
    except Exception:
        rate_now = 0
    if not allowed:
        # Fail-closed: log + raise, no SDK call.
        _log_call(source=source, method=method_name, status="blocked",
                   latency_ms=0.0, blocked_ms=blocked_ms,
                   error_class="AlpacaRateLimitBlocked",
                   extra={"rate_per_min": rate_now,
                          "guard_max_per_min": getattr(guard, "max_per_min", None),
                          "timeout_sec": block_timeout_sec})
        log.warning("Alpaca call BLOCKED by rate-guard: %s.%s "
                     "(waited %.1fs, current rate %d/min, cap %s/min)",
                     source, method_name, blocked_ms / 1000.0, rate_now,
                     getattr(guard, "max_per_min", "?"))
        raise AlpacaRateLimitBlocked(
            f"{source}.{method_name} blocked: rate-guard budget exhausted "
            f"(waited {blocked_ms:.0f}ms, current {rate_now}/min)"
        )
    t_call_start = time.monotonic()
    try:
        result = callable_fn(*args, **kwargs)
        latency_ms = (time.monotonic() - t_call_start) * 1000
        _log_call(source=source, method=method_name, status="ok",
                   latency_ms=latency_ms, blocked_ms=blocked_ms,
                   extra={"rate_per_min": rate_now})
        return result
    except Exception as e:
        latency_ms = (time.monotonic() - t_call_start) * 1000
        _log_call(source=source, method=method_name, status="error",
                   latency_ms=latency_ms, blocked_ms=blocked_ms,
                   error_class=type(e).__name__,
                   extra={"error": str(e)[:200], "rate_per_min": rate_now})
        raise


class _GuardedProxy:
    """Wraps any object so EVERY attribute access that returns a
    callable goes through the rate-guard + JSONL logger. Non-callable
    attributes are passed through unchanged."""

    def __init__(self, inner, *, source: str, guard):
        self._inner = inner
        self._source = source
        self._guard = guard

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            return _guarded_invoke(
                guard=self._guard, source=self._source,
                method_name=name, callable_fn=attr,
                args=args, kwargs=kwargs,
            )

        return wrapped


def GuardedTradingClient(*args, **kwargs):
    """Drop-in replacement for alpaca.trading.client.TradingClient.

    Same constructor signature. All HTTP-triggering methods go through
    the module-global RateGuard and log to alpaca_api_calls.jsonl."""
    from alpaca.trading.client import TradingClient
    from alpaca_rate_guard import get_global_guard
    inner = TradingClient(*args, **kwargs)
    return _GuardedProxy(inner, source="alpaca-trading",
                          guard=get_global_guard())


def GuardedStockHistoricalDataClient(*args, **kwargs):
    """Drop-in replacement for alpaca.data.historical.StockHistoricalDataClient."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca_rate_guard import get_global_guard
    inner = StockHistoricalDataClient(*args, **kwargs)
    return _GuardedProxy(inner, source="alpaca-data",
                          guard=get_global_guard())


def current_rate_per_min() -> int:
    """Live metric: how many guarded Alpaca calls happened in the last
    60 seconds. Suitable for status_dashboard + health-monitor probes."""
    try:
        from alpaca_rate_guard import get_global_guard
        return get_global_guard().current_rate_per_min
    except Exception:
        return 0
