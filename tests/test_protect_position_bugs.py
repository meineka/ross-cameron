"""Audit-Iter 7 (2026-05-12): protect_position OCO-vs-separate-orders bug.

CRITICAL Bug BO-1: protect_position submitted Stop + TP als SEPARATE Orders.
Wenn Stop fillt, blieb TP offen — wenn Preis später das TP-Level streifte,
wurde nochmal verkauft → Account SHORT.

Jetzt: OCO atomic, One-Cancels-Other beim Broker.

Plus Tests für:
  BO-3: error-escalation (mind. Stop muss stehen)
  BO-5: invalid stop/tp Pre-Check
  BO-8: shares=0 wird sichtbar geloggt statt silent skip
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
    ex.client.get_orders.return_value = []  # cancel_open_orders_for
    ex.dry_run = False
    return ex


# ─── Bug BO-1: OCO statt separate Orders ─────────────────────────────────────
def test_protect_position_uses_oco_not_separate_orders():
    """CRITICAL: muss EINE OCO-Order submitten, nicht zwei separate."""
    from alpaca.trading.enums import OrderClass
    ex = _make_executor()
    ex.client.submit_order.return_value = MagicMock(id="oco-1")
    ok = ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0)
    assert ok is True
    # Genau 1 Order (OCO), nicht 2
    assert ex.client.submit_order.call_count == 1
    req = ex.client.submit_order.call_args.args[0]
    assert req.order_class == OrderClass.OCO
    assert req.qty == 10
    # Stop + TP sind nested params der OCO
    assert req.stop_loss is not None
    assert req.take_profit is not None
    assert float(req.stop_loss.stop_price) == 99.0
    assert float(req.take_profit.limit_price) == 110.0


def test_protect_position_returns_false_on_invalid_levels():
    """Stop >= TP wäre ungültig — muss früh raus, NICHT submitten."""
    ex = _make_executor()
    assert ex.protect_position("AAPL", 10, stop=110.0, take_profit=110.0) is False
    assert ex.protect_position("AAPL", 10, stop=120.0, take_profit=110.0) is False
    ex.client.submit_order.assert_not_called()


def test_protect_position_returns_false_when_shares_zero():
    """shares=0 darf nicht submitten — muss geloggt werden (BO-8)."""
    ex = _make_executor()
    assert ex.protect_position("AAPL", 0, stop=99.0, take_profit=110.0) is False
    ex.client.submit_order.assert_not_called()


def test_protect_position_returns_true_in_dry_run():
    ex = _make_executor()
    ex.dry_run = True
    assert ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0) is True
    ex.client.submit_order.assert_not_called()


# ─── Bug BO-3: Fallback wenn OCO failed ──────────────────────────────────────
def test_falls_back_to_separate_orders_when_oco_fails():
    """Wenn OCO rejected (z.B. Broker-Glitch), muss mindestens Stop+TP separat
    versucht werden — return-value spiegelt ob Stop wirklich steht."""
    ex = _make_executor()
    # OCO failed, dann beide Fallback-Orders OK
    ex.client.submit_order.side_effect = [
        RuntimeError("OCO not supported on this account-tier"),  # OCO
        MagicMock(id="stop-1"),                                    # StopOrder fallback
        MagicMock(id="tp-1"),                                      # LimitOrder fallback
    ]
    ok = ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0)
    assert ok is True  # mindestens Stop steht
    assert ex.client.submit_order.call_count == 3


def test_fallback_returns_false_when_stop_also_fails():
    """OCO failed UND Stop failed → return False = position UNGESCHÜTZT."""
    ex = _make_executor()
    ex.client.submit_order.side_effect = [
        RuntimeError("OCO failed"),
        RuntimeError("Stop also failed"),
        MagicMock(id="tp-1"),  # nur TP geht — Position trotzdem unprotected
    ]
    ok = ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0)
    assert ok is False  # KEIN Stop → caller weiß: warning


# ─── Pre-Cancel-Pattern (Order-Hygiene) ──────────────────────────────────────
def test_cancels_old_orders_before_new_oco():
    """Alte Bracket-Children müssen weg sonst Doppel-Sell-Risiko."""
    ex = _make_executor()
    old_order = MagicMock()
    old_order.id = "old-child-1"
    ex.client.get_orders.return_value = [old_order]
    ex.client.submit_order.return_value = MagicMock(id="oco-new")
    ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0)
    ex.client.cancel_order_by_id.assert_called_with("old-child-1")


# ─── Sanity-Trace: KEINE 2 SELL-Orders für volle Shares ──────────────────────
def test_never_creates_two_full_sells_for_same_position():
    """Selbst nach OCO-Submission DARF die OCO als logische Einheit zählen —
    keine 2 separaten StopOrderRequest + LimitOrderRequest existieren parallel."""
    from alpaca.trading.requests import StopOrderRequest, LimitOrderRequest
    ex = _make_executor()
    ex.client.submit_order.return_value = MagicMock(id="oco-1")
    ex.protect_position("AAPL", 10, stop=99.0, take_profit=110.0)
    # Inspiziere alle submit_order-Calls: keiner soll StopOrderRequest sein
    # (das war der alte Bug — jetzt nur OCO LimitOrderRequest mit nested params)
    types = [type(c.args[0]).__name__ for c in ex.client.submit_order.call_args_list]
    # Nur 1 Order, kein StopOrderRequest:
    assert "StopOrderRequest" not in types
    assert types.count("LimitOrderRequest") == 1
