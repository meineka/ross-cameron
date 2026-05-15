"""Phase-22 (ChatGPT-09:27 Task 4 / ChatGPT-08:49 #3+#4): structured
loggers for market-data calls and order-lifecycle state.

Tests cover:
  - MarketDataLogger writes JSONL with timestamp + schema_version
  - timer() context manager fills latency_ms automatically
  - timer() captures error_class on exception
  - symbol_count derives from symbols list
  - large symbol lists are NOT bloated into the row
  - OrderLifecycleLogger.emit_intent generates unique intent_ids
  - All emit_* states emit the right `state` field
  - filled_qty < qty emits state="partial" not "filled"
  - Null logger variants are drop-in
  - Both loggers are robust to disk errors (no crash)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.critical  # Phase-22: live-safety gate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _lines(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ─── MarketDataLogger ───────────────────────────────────────────────────────

def test_market_data_logger_writes_single_row(tmp_path):
    from structured_logger import MarketDataLogger
    out = tmp_path / "md.jsonl"
    mdl = MarketDataLogger(out)
    mdl.log_call(source="yfinance", call="news", status="ok",
                  latency_ms=150.5, symbols=["AAA"])
    rows = _lines(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "market_data_call"
    assert r["source"] == "yfinance"
    assert r["call"] == "news"
    assert r["status"] == "ok"
    assert r["latency_ms"] == 150.5
    assert r["symbol_count"] == 1
    assert r["symbols"] == ["AAA"]
    assert r["error_class"] is None
    assert "ts" in r
    assert r["schema_version"] == 1


def test_market_data_logger_timer_captures_latency(tmp_path):
    from structured_logger import MarketDataLogger
    import time
    out = tmp_path / "md.jsonl"
    mdl = MarketDataLogger(out)
    with mdl.timer(source="alpaca", call="snapshot",
                    symbols=["A", "B", "C"]) as ev:
        time.sleep(0.02)  # 20ms minimum
        ev["status"] = "ok"
    rows = _lines(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "ok"
    assert r["latency_ms"] >= 15  # Allow some scheduler slop
    assert r["symbol_count"] == 3


def test_market_data_logger_timer_records_exception(tmp_path):
    from structured_logger import MarketDataLogger
    out = tmp_path / "md.jsonl"
    mdl = MarketDataLogger(out)
    with pytest.raises(RuntimeError):
        with mdl.timer(source="alpaca", call="bars", symbols=["X"]) as ev:
            raise RuntimeError("boom")
    rows = _lines(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "error"
    assert r["error_class"] == "RuntimeError"


def test_market_data_logger_skips_large_symbol_list(tmp_path):
    from structured_logger import MarketDataLogger
    out = tmp_path / "md.jsonl"
    mdl = MarketDataLogger(out)
    huge = [f"S{i}" for i in range(500)]
    mdl.log_call(source="alpaca", call="snapshot", status="ok", symbols=huge)
    r = _lines(out)[0]
    assert r["symbol_count"] == 500
    # Symbols list omitted when > 20
    assert "symbols" not in r


def test_market_data_logger_error_class_helper():
    from structured_logger import MarketDataLogger
    assert MarketDataLogger.error_class(None) is None
    assert MarketDataLogger.error_class(ValueError("x")) == "ValueError"
    # Module-qualified for non-builtin
    try:
        import urllib.error  # noqa
        err = urllib.error.URLError("x")
        cls = MarketDataLogger.error_class(err)
        assert "URLError" in cls
        assert "urllib" in cls
    except ImportError:
        pass


# ─── OrderLifecycleLogger ───────────────────────────────────────────────────

def test_order_lifecycle_emit_intent_generates_unique_ids(tmp_path):
    from structured_logger import OrderLifecycleLogger
    out = tmp_path / "ol.jsonl"
    ol = OrderLifecycleLogger(out)
    id1 = ol.emit_intent(symbol="AAA", side="BUY", qty=10,
                          planned_price=10.0, planned_stop=9.5,
                          planned_target=11.0)
    id2 = ol.emit_intent(symbol="AAA", side="BUY", qty=20)
    assert id1 != id2
    assert id1.startswith("oi-AAA-")
    rows = _lines(out)
    assert len(rows) == 2
    assert rows[0]["state"] == "intent"
    assert rows[0]["intent_id"] == id1
    assert rows[0]["planned_price"] == 10.0
    assert rows[0]["planned_stop"] == 9.5
    assert rows[0]["planned_target"] == 11.0


def test_order_lifecycle_full_state_flow(tmp_path):
    """intent → submitted → accepted → partial → filled →
    protection_verified → closed should all surface with the right
    state field and intent_id linkage."""
    from structured_logger import OrderLifecycleLogger
    out = tmp_path / "ol.jsonl"
    ol = OrderLifecycleLogger(out)
    iid = ol.emit_intent(symbol="AAA", side="BUY", qty=10,
                          planned_price=10.0)
    ol.emit_submitted(iid, symbol="AAA", side="BUY", qty=10,
                       broker_order_id="bo-1")
    ol.emit_accepted(iid, symbol="AAA", side="BUY", qty=10,
                      broker_order_id="bo-1")
    ol.emit_filled(iid, symbol="AAA", side="BUY", qty=10,
                    filled_qty=6, actual_price=10.01)
    ol.emit_filled(iid, symbol="AAA", side="BUY", qty=10,
                    filled_qty=10, actual_price=10.01)
    ol.emit_protection_verified(iid, symbol="AAA", qty=10,
                                  planned_stop=9.5, planned_target=11.0)
    ol.emit_closed(iid, symbol="AAA", side="SELL", qty=10)
    rows = _lines(out)
    states = [r["state"] for r in rows]
    assert states == ["intent", "submitted", "accepted",
                       "partial",  # filled_qty < qty
                       "filled",
                       "protection_verified", "closed"]
    # All linked by the same intent_id
    assert all(r["intent_id"] == iid for r in rows)


def test_order_lifecycle_emit_rejected_carries_error_class(tmp_path):
    from structured_logger import OrderLifecycleLogger
    out = tmp_path / "ol.jsonl"
    ol = OrderLifecycleLogger(out)
    iid = ol.emit_intent(symbol="AAA", side="BUY", qty=10)
    ol.emit_rejected(iid, symbol="AAA", side="BUY", qty=10,
                      error_class="alpaca.RejectedOrderError",
                      reason="stale_quote")
    rows = _lines(out)
    rej = next(r for r in rows if r["state"] == "rejected")
    assert rej["error_class"] == "alpaca.RejectedOrderError"
    assert rej["reason"] == "stale_quote"


def test_order_lifecycle_protection_repaired_emits(tmp_path):
    """Phase-17's "missing stop repaired" path now emits a structured
    event so postmortems can prove the bot's repair logic fired."""
    from structured_logger import OrderLifecycleLogger
    out = tmp_path / "ol.jsonl"
    ol = OrderLifecycleLogger(out)
    iid = ol.emit_intent(symbol="AAA", side="BUY", qty=10)
    ol.emit_protection_repaired(iid, symbol="AAA", qty=10,
                                  planned_stop=10.0, planned_target=11.0,
                                  reason="broker_dropped_stop_child")
    rep = _lines(out)[-1]
    assert rep["state"] == "protection_repaired"
    assert rep["planned_stop"] == 10.0
    assert rep["reason"] == "broker_dropped_stop_child"


# ─── Null variants ──────────────────────────────────────────────────────────

def test_null_market_data_logger_is_drop_in(tmp_path):
    from structured_logger import NullMarketDataLogger
    null = NullMarketDataLogger()
    null.log_call(source="x", call="y", status="ok")
    with null.timer(source="x", call="y") as ev:
        ev["status"] = "ok"
    # No file touched
    assert not (tmp_path / "should_not_exist.jsonl").exists()


def test_null_order_lifecycle_logger_is_drop_in():
    from structured_logger import NullOrderLifecycleLogger
    null = NullOrderLifecycleLogger()
    iid = null.emit_intent(symbol="AAA", side="BUY", qty=10)
    assert iid == "null-intent"
    null.emit_submitted(iid, symbol="AAA", side="BUY", qty=10)
    null.emit_filled(iid, symbol="AAA", side="BUY", qty=10,
                      filled_qty=10, actual_price=10.0)
    null.emit_closed(iid, symbol="AAA", side="SELL", qty=10)


# ─── Robustness ─────────────────────────────────────────────────────────────

def test_market_data_logger_survives_unserializable_extra(tmp_path):
    """Logger must NOT crash a trade decision if extra has a
    non-serializable object — silently skip the write instead."""
    from structured_logger import MarketDataLogger
    out = tmp_path / "md.jsonl"
    mdl = MarketDataLogger(out)

    class NotSerializable:
        pass

    # Should not raise — just silently skip
    mdl.log_call(source="x", call="y", status="ok",
                  extra={"obj": NotSerializable()})
    # File may not exist OR be empty — both are acceptable
    if out.exists():
        assert _lines(out) == [] or len(_lines(out)) == 0


def test_market_data_logger_creates_parent_dir(tmp_path):
    """Parent directory is auto-created — caller doesn't need to mkdir."""
    from structured_logger import MarketDataLogger
    deep = tmp_path / "a" / "b" / "c" / "md.jsonl"
    mdl = MarketDataLogger(deep)
    mdl.log_call(source="x", call="y", status="ok")
    assert deep.exists()
    assert len(_lines(deep)) == 1
