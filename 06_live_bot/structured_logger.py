"""structured_logger.py — Phase-22 (ChatGPT-09:27 Task 4 / ChatGPT-08:49 #3+#4).

Two append-only JSONL loggers that make external-data and order-lifecycle
events fully diagnosable after the fact:

  MarketDataLogger -> market_data_calls.jsonl
    Every external market-data call (yfinance, Alpaca snapshot/bars/
    quotes, preflight checks) emits ONE row with:
      ts, schema_version, source, call, status, latency_ms,
      symbol_count, error_class, retry_count, extra

  OrderLifecycleLogger -> order_lifecycle.jsonl
    Every order transitions through:
      intent -> submitted -> accepted -> filled/partial/rejected/canceled
      -> protection_verified -> closed
    One row per transition; keyed by (intent_id, symbol).

Both loggers are append-only JSONL with explicit fsync on each line
(mirroring TradeLogger's durability guarantees from Phase 11).
Both expose a Null variant so tests / sweeps can disable persistence
without touching production paths.

Design notes:
- These loggers are NOT replacements for trades_live.jsonl. That ledger
  keeps the PnL truth. structured_logger.py captures the OPERATIONAL
  layer that postmortems need (latency, error class, who-rejected-why).
- All write paths are wrapped in try/except so a logging failure never
  crashes a trade-decision codepath.
"""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SCHEMA_VERSION_MARKETDATA = 1
SCHEMA_VERSION_ORDERLIFECYCLE = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_call_class(err: BaseException | None) -> Optional[str]:
    if err is None:
        return None
    # e.g. "requests.exceptions.HTTPError" or "TimeoutError"
    cls = type(err)
    mod = cls.__module__
    if mod in ("builtins", "__main__"):
        return cls.__name__
    return f"{mod}.{cls.__name__}"


class _BaseAppendOnlyLogger:
    """Common machinery: lock, fsync, error-tolerance."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _write_event(self, event: dict) -> None:
        try:
            line = json.dumps(event) + "\n"
        except (TypeError, ValueError):
            # If something isn't serializable, skip (don't crash the trade)
            return
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        pass
        except (OSError, IOError):
            # disk full / locked file — never block the caller
            return


class MarketDataLogger(_BaseAppendOnlyLogger):
    """Phase-22: one JSONL row per external market-data call.

    Typical call sites:
      - yfinance daily-bar fetch
      - yfinance ticker news (catalyst probe)
      - alpaca StockSnapshotRequest
      - alpaca StockBarsRequest (extended-hours / RTH)
      - alpaca StockQuotesRequest
      - preflight: account-equity, get_all_positions

    Use the `timer()` context manager so latency_ms is captured
    consistently:

        mdl = MarketDataLogger("market_data_calls.jsonl")
        with mdl.timer(source="yfinance", call="news",
                        symbols=["AAA"]) as ev:
            try:
                result = yf.Ticker("AAA").news
                ev["status"] = "ok"
                ev["extra"] = {"items": len(result)}
            except Exception as e:
                ev["status"] = "error"
                ev["error_class"] = MarketDataLogger.error_class(e)
                raise
    """

    @staticmethod
    def error_class(err: BaseException | None) -> Optional[str]:
        return _safe_call_class(err)

    def log_call(self, *, source: str, call: str, status: str,
                  latency_ms: float | None = None,
                  symbols: list[str] | None = None,
                  symbol_count: int | None = None,
                  error_class: str | None = None,
                  retry_count: int = 0,
                  extra: dict | None = None) -> None:
        if symbol_count is None and symbols is not None:
            symbol_count = len(symbols)
        ev = {
            "ts": _now_iso(),
            "schema_version": SCHEMA_VERSION_MARKETDATA,
            "kind": "market_data_call",
            "source": source,
            "call": call,
            "status": status,
            "latency_ms": latency_ms,
            "symbol_count": symbol_count,
            "error_class": error_class,
            "retry_count": retry_count,
        }
        if symbols is not None and len(symbols) <= 20:
            # Don't bloat the log with 5000-symbol lists
            ev["symbols"] = list(symbols)
        if extra:
            ev["extra"] = extra
        self._write_event(ev)

    def timer(self, **fixed):
        """Context manager that auto-fills latency_ms + emits on exit.
        Caller mutates the yielded dict to set status / error_class /
        extra; the actual write happens on __exit__."""
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner._t0 = time.perf_counter()
                self_inner.event = dict(fixed)
                self_inner.event.setdefault("status", "ok")
                return self_inner.event

            def __exit__(self_inner, exc_type, exc_val, tb):
                latency_ms = (time.perf_counter() - self_inner._t0) * 1000
                ev = self_inner.event
                ev.setdefault("latency_ms", round(latency_ms, 2))
                if exc_type is not None and ev.get("status") == "ok":
                    ev["status"] = "error"
                    ev["error_class"] = outer.error_class(exc_val)
                outer.log_call(**ev)
                return False  # never swallow

        return _Ctx()


class OrderLifecycleLogger(_BaseAppendOnlyLogger):
    """Phase-22: one JSONL row per order lifecycle state transition.

    State machine (each is a `state` field value):
      intent              — bot decided to place an order; pre-broker
      submitted           — order sent to broker, awaiting accept
      accepted            — broker acknowledged the order
      rejected            — broker refused (with error_class + reason)
      partial             — partial fill received
      filled              — full fill received
      canceled            — order canceled (by bot, by broker, or by bracket parent)
      protection_verified — bracket stop+target observed alive after entry fill
      protection_repaired — missing stop/target re-submitted
      closed              — position flat; all bracket children resolved

    The intent_id ties all events for one logical trade together. Use
    `start_intent(symbol, side, qty, planned_*)` to generate it.
    """

    def __init__(self, path: Path | str):
        super().__init__(path)
        self._intent_seq = 0
        self._intent_lock = threading.Lock()

    def _gen_intent_id(self, symbol: str) -> str:
        with self._intent_lock:
            self._intent_seq += 1
            seq = self._intent_seq
        return f"oi-{symbol}-{int(time.time() * 1000)}-{seq}"

    def _log_state(self, *, intent_id: str, symbol: str, side: str,
                    qty: int, state: str,
                    planned_price: float | None = None,
                    planned_stop: float | None = None,
                    planned_target: float | None = None,
                    actual_price: float | None = None,
                    filled_qty: int | None = None,
                    broker_order_id: str | None = None,
                    client_order_id: str | None = None,
                    error_class: str | None = None,
                    reason: str | None = None,
                    extra: dict | None = None) -> None:
        ev = {
            "ts": _now_iso(),
            "schema_version": SCHEMA_VERSION_ORDERLIFECYCLE,
            "kind": "order_lifecycle",
            "intent_id": intent_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "state": state,
            "planned_price": planned_price,
            "planned_stop": planned_stop,
            "planned_target": planned_target,
            "actual_price": actual_price,
            "filled_qty": filled_qty,
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id,
            "error_class": error_class,
            "reason": reason,
        }
        if extra:
            ev["extra"] = extra
        self._write_event(ev)

    # ─── Public state-emit API ──────────────────────────────────────────
    def emit_intent(self, *, symbol: str, side: str, qty: int,
                     planned_price: float | None = None,
                     planned_stop: float | None = None,
                     planned_target: float | None = None,
                     extra: dict | None = None) -> str:
        """Generate a new intent_id and emit the initial `intent` row.
        Returns the intent_id so the caller can thread it through
        subsequent emit_* calls."""
        intent_id = self._gen_intent_id(symbol)
        self._log_state(
            intent_id=intent_id, symbol=symbol, side=side, qty=qty,
            state="intent", planned_price=planned_price,
            planned_stop=planned_stop, planned_target=planned_target,
            extra=extra,
        )
        return intent_id

    def emit_submitted(self, intent_id: str, *, symbol: str, side: str,
                        qty: int, broker_order_id: str | None = None,
                        client_order_id: str | None = None,
                        extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state="submitted",
                         broker_order_id=broker_order_id,
                         client_order_id=client_order_id, extra=extra)

    def emit_accepted(self, intent_id: str, *, symbol: str, side: str,
                       qty: int, broker_order_id: str | None = None,
                       extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state="accepted",
                         broker_order_id=broker_order_id, extra=extra)

    def emit_rejected(self, intent_id: str, *, symbol: str, side: str,
                       qty: int, error_class: str | None = None,
                       reason: str | None = None,
                       broker_order_id: str | None = None,
                       extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state="rejected",
                         error_class=error_class, reason=reason,
                         broker_order_id=broker_order_id, extra=extra)

    def emit_filled(self, intent_id: str, *, symbol: str, side: str,
                     qty: int, filled_qty: int, actual_price: float,
                     broker_order_id: str | None = None,
                     extra: dict | None = None) -> None:
        state = "filled" if filled_qty >= qty else "partial"
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state=state, filled_qty=filled_qty,
                         actual_price=actual_price,
                         broker_order_id=broker_order_id, extra=extra)

    def emit_canceled(self, intent_id: str, *, symbol: str, side: str,
                       qty: int, reason: str | None = None,
                       broker_order_id: str | None = None,
                       extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state="canceled", reason=reason,
                         broker_order_id=broker_order_id, extra=extra)

    def emit_protection_verified(self, intent_id: str, *, symbol: str,
                                   qty: int, planned_stop: float,
                                   planned_target: float,
                                   extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side="SELL",
                         qty=qty, state="protection_verified",
                         planned_stop=planned_stop,
                         planned_target=planned_target, extra=extra)

    def emit_protection_repaired(self, intent_id: str, *, symbol: str,
                                   qty: int, planned_stop: float,
                                   planned_target: float,
                                   reason: str | None = None,
                                   extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side="SELL",
                         qty=qty, state="protection_repaired",
                         planned_stop=planned_stop,
                         planned_target=planned_target,
                         reason=reason, extra=extra)

    def emit_closed(self, intent_id: str, *, symbol: str, side: str,
                     qty: int, extra: dict | None = None) -> None:
        self._log_state(intent_id=intent_id, symbol=symbol, side=side,
                         qty=qty, state="closed", extra=extra)


class NullMarketDataLogger:
    """Drop-in no-op for tests / sweeps. Same .log_call/.timer API."""
    error_class = MarketDataLogger.error_class

    def log_call(self, **kw): pass

    def timer(self, **fixed):
        class _Ctx:
            def __enter__(self): self.event = dict(fixed); return self.event
            def __exit__(self, *a): return False
        return _Ctx()


class NullOrderLifecycleLogger:
    """Drop-in no-op for tests / sweeps."""
    def emit_intent(self, **kw) -> str: return "null-intent"
    def emit_submitted(self, *a, **kw): pass
    def emit_accepted(self, *a, **kw): pass
    def emit_rejected(self, *a, **kw): pass
    def emit_filled(self, *a, **kw): pass
    def emit_canceled(self, *a, **kw): pass
    def emit_protection_verified(self, *a, **kw): pass
    def emit_protection_repaired(self, *a, **kw): pass
    def emit_closed(self, *a, **kw): pass


# Default file paths (relative to this file's directory)
HERE = Path(__file__).resolve().parent
MARKET_DATA_PATH = HERE / "market_data_calls.jsonl"
ORDER_LIFECYCLE_PATH = HERE / "order_lifecycle.jsonl"
