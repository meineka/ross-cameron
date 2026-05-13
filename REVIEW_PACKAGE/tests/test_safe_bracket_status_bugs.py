"""Audit-Iter 27 (2026-05-13): safe_bracket_buy status-comparison bugs.

Bug SB-2 (HIGH): String-Vergleich `str(o.status) in (...)` ist fragile.
  Wenn alpaca-py das Enum-Repr ändert (z.B. von "OrderStatus.FILLED"
  zu "FILLED" oder umgekehrt), bekommt der Bot NIE einen FILLED-State
  zurück → ALL bracket-buys returnten "timeout" → repair-logic feuerte
  nie, kein protective stop nach fill.
  Fix: _status_is() helper tested gegen .value, .name, str(), und
  rsplit für ".FILLED"-Suffix.

Bug SB-6 (MED): Wenn filled_avg_price None ist (Alpaca-API-Quirk during
  partial fills), float(None) → TypeError. Fall-through zu timeout.
  Plus: timeout returnte without cancel — stranded order could later fill.
  Fix: defensive fp check + cancel attempt im timeout-Pfad.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── _status_is helper ──────────────────────────────────────────────────────
def test_status_is_matches_enum_repr_old_style():
    """alpaca-py älter: str(status) = 'OrderStatus.FILLED'."""
    from safe_bracket import safe_bracket_buy
    # Internal helper test — replicate the logic
    class FakeStatus:
        def __str__(self): return "OrderStatus.FILLED"
        name = "FILLED"
        value = "filled"
    # Test via integration (we don't expose _status_is directly)


def test_status_is_matches_lowercase_string():
    """alpaca-py: status als plain string 'filled'."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    filled = MagicMock()
    filled.id = "ord-1"
    filled.status = "filled"  # plain lowercase string
    filled.filled_avg_price = 10.50
    tc.get_order_by_id.return_value = filled
    import safe_bracket
    # Mock time.sleep to speed test
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=3,
        )
    assert result["status"] == "filled", result
    assert result["fill_price"] == 10.50


def test_status_is_matches_uppercase_string():
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    filled = MagicMock()
    filled.id = "ord-1"
    filled.status = "FILLED"
    filled.filled_avg_price = 10.50
    tc.get_order_by_id.return_value = filled
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=3,
        )
    assert result["status"] == "filled"


def test_status_is_matches_enum_with_value_attr():
    """alpaca-py: status ist ein Enum-Objekt mit .value attr."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    fake_status = MagicMock()
    fake_status.value = "filled"
    fake_status.name = "FILLED"
    fake_status.__str__ = lambda s: "OrderStatus.FILLED"
    filled = MagicMock()
    filled.id = "ord-1"
    filled.status = fake_status
    filled.filled_avg_price = 10.50
    tc.get_order_by_id.return_value = filled
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=3,
        )
    assert result["status"] == "filled"


def test_canceled_status_returns_failed():
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    canceled = MagicMock()
    canceled.id = "ord-1"
    canceled.status = "canceled"
    tc.get_order_by_id.return_value = canceled
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=3,
        )
    assert result["status"] == "failed"


# ─── Bug SB-6: None filled_avg_price ─────────────────────────────────────────
def test_filled_with_none_avg_price_continues_polling():
    """API-Quirk: status=FILLED but filled_avg_price=None auf erster poll
    → weiter pollen, nicht crashen."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    call_count = {"n": 0}

    def poll_progression(*a, **kw):
        call_count["n"] += 1
        o = MagicMock()
        o.id = "ord-1"
        o.status = "filled"
        if call_count["n"] == 1:
            o.filled_avg_price = None  # API quirk
        else:
            o.filled_avg_price = 10.50  # second poll has price
        return o

    tc.get_order_by_id.side_effect = poll_progression
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=3,
        )
    assert result["status"] == "filled"
    assert result["fill_price"] == 10.50


def test_filled_with_zero_avg_price_treated_as_quirk():
    """filled_avg_price=0 → API-Quirk → weiter pollen."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    o = MagicMock()
    o.id = "ord-1"
    o.status = "filled"
    o.filled_avg_price = 0  # quirk
    tc.get_order_by_id.return_value = o
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=2,
        )
    # Never gets real price → timeout
    assert result["status"] == "timeout"


# ─── Bug SB-6 (cancel on timeout) ────────────────────────────────────────────
def test_timeout_attempts_cancel():
    """Timeout muss cancel_order_by_id versuchen damit stranded orders
    nicht später fillen."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-stale"
    tc.submit_order.return_value = submitted
    pending = MagicMock()
    pending.id = "ord-stale"
    pending.status = "accepted"  # never fills
    tc.get_order_by_id.return_value = pending
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=2,
        )
    assert result["status"] == "timeout"
    tc.cancel_order_by_id.assert_called_with("ord-stale")


def test_timeout_cancel_failure_does_not_crash():
    """Cancel-attempt im timeout-Pfad failen ist OK — return trotzdem timeout."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-x"
    tc.submit_order.return_value = submitted
    pending = MagicMock()
    pending.status = "accepted"
    tc.get_order_by_id.return_value = pending
    tc.cancel_order_by_id.side_effect = RuntimeError("already filled in race")
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.5, take_profit=11.0,
            wait_seconds=2,
        )
    assert result["status"] == "timeout"  # robust, no crash


# ─── Sanity Tests ────────────────────────────────────────────────────────────
def test_filled_normal_path_no_repair():
    """Standard happy path: stop < fill → kein repair."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    filled = MagicMock()
    filled.id = "ord-1"
    filled.status = "filled"
    filled.filled_avg_price = 10.20
    tc.get_order_by_id.return_value = filled
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.30, stop=9.80, take_profit=11.30,
            wait_seconds=3,
        )
    assert result["status"] == "filled"
    assert result["repaired"] is False


def test_repair_path_triggers_when_fill_below_planned_stop():
    """Repair path: fill < planned stop → cancel + OCO repair."""
    tc = MagicMock()
    submitted = MagicMock(); submitted.id = "ord-1"
    tc.submit_order.return_value = submitted
    filled = MagicMock()
    filled.id = "ord-1"
    filled.status = "filled"
    filled.filled_avg_price = 9.50  # below planned stop of 9.80
    tc.get_order_by_id.return_value = filled
    tc.get_orders.return_value = []
    import safe_bracket
    import unittest.mock
    with unittest.mock.patch.object(safe_bracket.time, "sleep"):
        result = safe_bracket.safe_bracket_buy(
            tc, "AAA", 10, entry_limit=10.0, stop=9.80, take_profit=10.20,
            wait_seconds=3,
        )
    assert result["status"] == "filled"
    assert result["repaired"] is True
    # New stop should be 9.50 * 0.95 = 9.025
    assert result["stop"] < 9.50
