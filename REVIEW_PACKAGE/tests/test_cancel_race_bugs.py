"""Audit-Iter 8 (2026-05-12): cancel_open_orders_for race-condition bugs.

Bug BO-7: Vorher submitted cancel + returned sofort. Alpaca processiert
async (OPEN → PENDING_CANCEL → CANCELED). Folge-submit_sell konnte
während PENDING_CANCEL feuern → beide Sells alive → oversold (account
geht SHORT).

Bug BO-6: per-order cancel-Exception silent swallowed. Wenn 3 von 5
fehlschlagen, weiß caller nichts.

Jetzt:
  - Poll-Loop bis target-IDs nicht mehr OPEN sind (default 3s)
  - Failed cancels werden geloggt mit IDs
  - wait_seconds=0 = Legacy-Modus für Tests/Performance-Tuning
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_executor():
    import bot
    ex = bot.AlpacaExecutor.__new__(bot.AlpacaExecutor)
    ex.client = MagicMock()
    ex.dry_run = False
    return ex


def _order(oid: str):
    m = MagicMock()
    m.id = oid
    return m


# ─── Bug BO-7: Confirmation-Polling ──────────────────────────────────────────
def test_polls_until_cancels_confirmed():
    """Nach Cancel-Submit muss gepollt werden bis target-IDs nicht mehr OPEN."""
    ex = _make_executor()
    state = {"polls": 0}
    initial = [_order("o1"), _order("o2")]

    def get_orders_side(*a, **kw):
        state["polls"] += 1
        # Erster Call (initial list): beide da
        if state["polls"] == 1:
            return initial
        # Erster Poll (nach Cancel-Submit): o1 noch da
        if state["polls"] == 2:
            return [_order("o1")]
        # Zweiter Poll: beide weg
        return []

    ex.client.get_orders.side_effect = get_orders_side
    n = ex.cancel_open_orders_for("AAPL", wait_seconds=1.0, poll_interval=0.05)
    assert n == 2
    # Mindestens 3 get_orders Calls: initial + 2+ Polls
    assert state["polls"] >= 3


def test_returns_after_timeout_if_cancels_dont_confirm():
    """Wenn Polling ausläuft ohne dass IDs verschwinden → return + warning."""
    ex = _make_executor()
    # Initial 1 Order. Polling zeigt sie immer wieder.
    ex.client.get_orders.return_value = [_order("stuck-1")]
    n = ex.cancel_open_orders_for("AAPL", wait_seconds=0.2, poll_interval=0.05)
    # Cancel wurde submitted, return-count stimmt
    assert n == 1
    ex.client.cancel_order_by_id.assert_called_with("stuck-1")


def test_skips_polling_when_wait_seconds_zero():
    """Legacy/Performance-Modus: kein Polling = sofort return."""
    ex = _make_executor()
    ex.client.get_orders.return_value = [_order("o1"), _order("o2")]
    n = ex.cancel_open_orders_for("AAPL", wait_seconds=0.0)
    assert n == 2
    # Nur 1 get_orders Call (initial), keine Polls
    assert ex.client.get_orders.call_count == 1


# ─── Bug BO-6: Failed-Cancel Reporting ───────────────────────────────────────
def test_logs_failed_cancels_with_ids(caplog):
    """Per-order Exception darf nicht silent swallow — muss IDs loggen."""
    import logging
    ex = _make_executor()
    ex.client.get_orders.return_value = [_order("ok"), _order("fails")]

    def cancel_side(oid):
        if oid == "fails":
            raise RuntimeError("Order already filled")

    ex.client.cancel_order_by_id.side_effect = cancel_side
    with caplog.at_level(logging.WARNING):
        ex.cancel_open_orders_for("AAPL", wait_seconds=0.0)
    msgs = " ".join(r.message for r in caplog.records)
    assert "fails" in msgs
    assert "failed" in msgs.lower()


def test_partial_cancel_returns_only_submitted_count():
    """3 orders, 1 fails to cancel → return == 2, nicht 3."""
    ex = _make_executor()
    orders = [_order("a"), _order("b"), _order("c")]
    ex.client.get_orders.return_value = orders

    def cancel_side(oid):
        if oid == "b":
            raise RuntimeError("nope")

    ex.client.cancel_order_by_id.side_effect = cancel_side
    n = ex.cancel_open_orders_for("AAPL", wait_seconds=0.0)
    assert n == 2  # a + c canceled, b failed


# ─── Edge-Cases ──────────────────────────────────────────────────────────────
def test_no_open_orders_returns_zero():
    ex = _make_executor()
    ex.client.get_orders.return_value = []
    assert ex.cancel_open_orders_for("AAPL") == 0
    ex.client.cancel_order_by_id.assert_not_called()


def test_get_orders_api_failure_returns_zero():
    """API-Down beim Initial-List → return 0 statt crash."""
    ex = _make_executor()
    ex.client.get_orders.side_effect = RuntimeError("API down")
    assert ex.cancel_open_orders_for("AAPL") == 0


def test_dry_run_returns_zero_without_api_calls():
    ex = _make_executor()
    ex.dry_run = True
    assert ex.cancel_open_orders_for("AAPL") == 0
    ex.client.get_orders.assert_not_called()
    ex.client.cancel_order_by_id.assert_not_called()


# ─── Integration: submit_sell_limit nutzt cancel ─────────────────────────────
def test_submit_sell_limit_calls_cancel_first():
    """submit_sell_limit muss cancel_open_orders_for vor sell submitten."""
    ex = _make_executor()
    ex.client.get_orders.return_value = [_order("bracket-child")]
    ex.client.submit_order.return_value = MagicMock(id="sell-1")
    sid = ex.submit_sell_limit("AAPL", 5, 10.5, "T1_50pct")
    assert sid == "sell-1"
    # Cancel wurde aufgerufen vor submit_order
    ex.client.cancel_order_by_id.assert_called_with("bracket-child")
    ex.client.submit_order.assert_called_once()
