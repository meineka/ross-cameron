"""Tests: jede Position MUSS broker-seitig Stop + Take-Profit haben.

Garantiert dass wir nie 'nackt' im Markt sind: kein submit_buy ohne stop+tp,
T1-Partial re-protected die Restposition, Pyramiding-Add re-protected,
in-script-Sell cancelt erst Children.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Executor.submit_bracket_buy ─────────────────────────────────────────────
def test_executor_bracket_buy_sends_stop_and_tp():
    """Review-fix 2026-05-13: now returns dict with fill info, not order_id."""
    import bot
    import unittest.mock as _m
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    fake_order = MagicMock(id="abc-123")
    ex.client.submit_order.return_value = fake_order
    filled = MagicMock()
    filled.status = "filled"
    filled.filled_avg_price = 10.0
    filled.filled_qty = 10
    ex.client.get_order_by_id.return_value = filled
    with _m.patch("time.sleep"):
        result = ex.submit_bracket_buy("AAA", 10, entry=10.0, stop=9.0, take_profit=12.0,
                                         wait_fill_seconds=2)
    assert result["status"] == "filled"
    assert result["order_id"] == "abc-123"
    # Bracket-Konfiguration im submit-call
    from alpaca.trading.enums import OrderClass
    call = ex.client.submit_order.call_args[0][0]
    assert call.order_class == OrderClass.BRACKET
    assert call.stop_loss.stop_price == 9.0
    assert call.take_profit.limit_price == 12.0
    assert call.qty == 10


def test_executor_dry_run_bracket_no_real_call():
    """Dry-run returns filled-status simulating success."""
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=True)
    ex.client = MagicMock()
    result = ex.submit_bracket_buy("AAA", 5, 10, 9, 12)
    assert result["status"] == "filled"
    ex.client.submit_order.assert_not_called()


# ─── cancel_open_orders_for ──────────────────────────────────────────────────
def test_cancel_open_orders_iterates():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.get_orders.return_value = [MagicMock(id="o1"), MagicMock(id="o2")]
    n = ex.cancel_open_orders_for("AAA")
    assert n == 2
    assert ex.client.cancel_order_by_id.call_count == 2


# ─── protect_position ────────────────────────────────────────────────────────
def test_protect_position_cancels_then_resubmits_stop_and_tp():
    """Audit-Iter 7: jetzt OCO-Order (atomic) statt 2 separater Orders.
    Bug-Fix BO-1: separate Orders konnten oversold/SHORT auslösen."""
    from alpaca.trading.enums import OrderClass
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.get_orders.return_value = []
    ex.client.submit_order.return_value = MagicMock(id="oco-1")
    ex.protect_position("AAA", 5, stop=9.0, take_profit=11.0)
    # Genau EINE OCO-Order (atomic — Stop + TP nested)
    sides = [c[0][0] for c in ex.client.submit_order.call_args_list]
    assert len(sides) == 1
    oco = sides[0]
    assert oco.order_class == OrderClass.OCO
    assert float(oco.stop_loss.stop_price) == 9.0
    assert float(oco.take_profit.limit_price) == 11.0


# ─── submit_sell_limit cancelt erst Children ─────────────────────────────────
def test_sell_limit_cancels_brackets_first():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.get_orders.return_value = [MagicMock(id="bracket-1")]
    ex.client.submit_order.return_value = MagicMock(id="sell-id")
    ex.submit_sell_limit("AAA", 5, 10.5, "T1_50pct")
    # Cancel-Bracket vorher passiert
    ex.client.cancel_order_by_id.assert_called_with("bracket-1")
    # Dann sell submit
    ex.client.submit_order.assert_called_once()


# ─── Bot.try_enter benutzt Bracket statt plain BUY ───────────────────────────
def test_bot_entry_uses_bracket_in_source():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "submit_bracket_buy" in src
    # Im pattern-entry-Block (sucht nach "SUBMITTING BRACKET-BUY"):
    assert "SUBMITTING BRACKET-BUY" in src


def test_t1_partial_reprotects_remaining():
    """Nach T1-Partial muss protect_position für Rest aufgerufen werden.
    Review-V2 P0.1: now via submit_sell_with_confirm (fill-poll)."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Match new confirm-variant T1 block
    idx = src.find("submit_sell_with_confirm(\n                ts.symbol, half, ts.target1_price")
    assert idx > 0, "T1 block not found with confirm-variant"
    after = src[idx: idx + 1200]
    assert "protect_position" in after


def test_pyramiding_add_reprotects():
    """Nach Add: Bracket der originalen Position covert nicht mehr alle Shares
    → protect_position auf gesamte neue Position."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find("ADD-TO-WINNER")
    assert idx > 0
    # Look forward from the ADD log statement for protect_position
    after = src[idx: idx + 2000]
    assert "protect_position" in after


# ─── Smoke: Bot importiert weiterhin ────────────────────────────────────────
def test_bot_imports():
    import bot
    assert hasattr(bot, "AlpacaExecutor")
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=True)
    assert hasattr(ex, "submit_bracket_buy")
    assert hasattr(ex, "protect_position")
    assert hasattr(ex, "cancel_open_orders_for")
