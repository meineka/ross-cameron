"""Tests für den 2026-05-12 14:00 ET HSPT/ATRA-Bug:
Bracket-Stop war über tatsächlichem Fill → Alpaca rejected, Position ungeschützt."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.critical  # Phase-21 (ChatGPT-09:15 #1): live-safety gate
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Pre-Submit-Sanity-Check ─────────────────────────────────────────────────
def test_bracket_buy_rejects_stop_above_entry():
    """Review-fix 2026-05-13: return-shape changed from order_id|None
    to dict with 'status' field."""
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    result = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=10.50, take_profit=12.0)
    assert result["status"] == "failed"
    ex.client.submit_order.assert_not_called()


def test_bracket_buy_rejects_tp_below_entry():
    import bot
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    result = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=9.0, take_profit=9.5)
    assert result["status"] == "failed"


def test_bracket_buy_accepts_valid_returns_filled_dict():
    """Valid bracket should now poll for fill and return filled status."""
    import bot
    import unittest.mock as _m
    ex = bot.AlpacaExecutor("k", "s", paper=True, dry_run=False)
    ex.client = MagicMock()
    ex.client.submit_order.return_value = MagicMock(id="oid")
    # Mock fill response
    filled_order = MagicMock()
    filled_order.status = "filled"
    filled_order.filled_avg_price = 10.05
    filled_order.filled_qty = 5
    ex.client.get_order_by_id.return_value = filled_order
    with _m.patch("time.sleep"):
        result = ex.submit_bracket_buy("AAA", 5, entry=10.0, stop=9.5, take_profit=11.0,
                                         wait_fill_seconds=2)
    assert result["status"] == "filled"
    assert result["order_id"] == "oid"
    assert result["fill_price"] == 10.05
    assert result["shares"] == 5


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
    assert hasattr(safe_bracket, "check_liquidity")
    assert hasattr(safe_bracket, "quote_based_entry")


# ─── Liquidity-Check (HSPT-style detection) ──────────────────────────────────
def test_liquidity_rejects_low_daily_volume():
    """HSPT-Profil: 188 daily volume, fake last_trade $10.55."""
    from safe_bracket import check_liquidity
    snap = MagicMock()
    snap.daily_bar.volume = 188
    snap.latest_quote.bid_price = 7.50
    snap.latest_quote.ask_price = 8.10
    ok, reason = check_liquidity(snap)
    assert ok is False
    assert "daily_volume" in reason


def test_liquidity_rejects_no_two_sided_quote():
    """HSPT-Realität: ask=0 (kein Verkäufer)."""
    from safe_bracket import check_liquidity
    snap = MagicMock()
    snap.daily_bar.volume = 100_000
    snap.latest_quote.bid_price = 7.50
    snap.latest_quote.ask_price = 0
    ok, reason = check_liquidity(snap)
    assert ok is False
    assert "no two-sided quote" in reason


def test_liquidity_rejects_huge_spread():
    """Wide spread = illiquide. Bid 5/Ask 10 = 67 % spread."""
    from safe_bracket import check_liquidity
    snap = MagicMock()
    snap.daily_bar.volume = 100_000
    snap.latest_quote.bid_price = 5.0
    snap.latest_quote.ask_price = 10.0
    ok, reason = check_liquidity(snap)
    assert ok is False
    assert "spread" in reason


def test_liquidity_accepts_healthy_stock():
    """AAPL-Profil: tight spread, hohe Vol."""
    from safe_bracket import check_liquidity
    snap = MagicMock()
    snap.daily_bar.volume = 5_000_000
    snap.latest_quote.bid_price = 294.19
    snap.latest_quote.ask_price = 294.22
    ok, reason = check_liquidity(snap)
    assert ok is True


def test_quote_based_entry_uses_ask_not_last_trade():
    """Kern-Fix: entry basiert auf ask, NICHT latest_trade.price.
    Im HSPT-Fall war last_trade=$10.55 (stale), ask wäre ~$8.10."""
    from safe_bracket import quote_based_entry
    snap = MagicMock()
    snap.latest_quote.ask_price = 8.10
    snap.latest_quote.bid_price = 7.50
    plan = quote_based_entry(snap)
    assert 8.10 <= plan["entry"] <= 8.15  # ask + slippage
    assert plan["stop"] < plan["entry"]    # valid for long
    assert plan["tp"] > plan["entry"]      # valid TP
    # 1:2 R:R approximately
    risk = plan["entry"] - plan["stop"]
    reward = plan["tp"] - plan["entry"]
    assert 1.5 * risk <= reward <= 2.5 * risk
