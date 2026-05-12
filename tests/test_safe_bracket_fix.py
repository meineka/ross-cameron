"""Tests für den 2026-05-12 14:00 ET HSPT/ATRA-Bug:
Bracket-Stop war über tatsächlichem Fill → Alpaca rejected, Position ungeschützt."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Pre-Submit-Sanity-Check ─────────────────────────────────────────────────
def test_bracket_buy_rejects_stop_above_entry():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    rid = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=10.50, take_profit=12.0)
    assert rid is None
    ex.client.submit_order.assert_not_called()


def test_bracket_buy_rejects_tp_below_entry():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    rid = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=9.0, take_profit=9.5)
    assert rid is None


def test_bracket_buy_accepts_valid():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.submit_order.return_value = MagicMock(id="oid")
    rid = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=9.5, take_profit=11.0)
    assert rid == "oid"


# ─── Post-Fill-Repair ────────────────────────────────────────────────────────
def test_verify_and_repair_when_stop_above_fill():
    """Reproduziert exakt HSPT-Bug: entry $10.60 plan, fill $8.11, stop $10.50."""
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.get_orders.return_value = [MagicMock(id="old-tp")]
    ex.client.submit_order.return_value = MagicMock(id="oco-id")
    repaired = ex.verify_and_repair_protection(
        "HSPT", fill_price=8.11, planned_stop=10.50, planned_tp=10.80, shares=5,
    )
    assert repaired is True
    ex.client.cancel_order_by_id.assert_called_with("old-tp")
    # OCO submit aufgerufen
    assert ex.client.submit_order.called
    args = ex.client.submit_order.call_args[0][0]
    # Stop muss UNTER fill (8.11) sein
    assert args.stop_loss.stop_price < 8.11
    # Stop ~5% unter fill = ~7.70
    assert 7.5 < args.stop_loss.stop_price < 8.0


def test_no_repair_needed_when_stop_below_fill():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    # Normal: fill $10.05, stop $9.50 — alles ok
    repaired = ex.verify_and_repair_protection(
        "ATRA", fill_price=10.05, planned_stop=9.50, planned_tp=11.00, shares=5,
    )
    assert repaired is False
    ex.client.submit_order.assert_not_called()


# ─── Safe-Bracket-Helper ─────────────────────────────────────────────────────
def test_safe_bracket_rejects_invalid_stop():
    from safe_bracket import safe_bracket_buy
    tc = MagicMock()
    result = safe_bracket_buy(tc, "AAA", 5, entry_limit=10.0, stop=11.0, take_profit=12.0)
    assert result["status"] == "failed"
    tc.submit_order.assert_not_called()


def test_safe_bracket_imports():
    import safe_bracket
    assert hasattr(safe_bracket, "safe_bracket_buy")
