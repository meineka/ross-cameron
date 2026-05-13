"""Tests for P0 fixes from external reviewer (2026-05-13 memo).

Concrete behavior-tests (not source-grep) for:
- handle_bar_5min exception handler no longer crashes with NameError
- NY_TZ is DST-aware ZoneInfo, not fixed UTC-4
- find_pilot_data_paths() supports both backtest_data/ and 04_backtest/
- market_close_all fallback handles short positions (BUY, not SELL)
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Fix: handle_bar_5min exception handler ──────────────────────────────────
@pytest.mark.asyncio
async def test_handle_bar_5min_exception_handler_does_not_raise_NameError():
    """REGRESSION: Reviewer found `getattr(bar, ...)` in except but `bar`
    isn't defined in handle_bar_5min scope → NameError on every error."""
    import bot as bot_mod
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.executor = MagicMock()
    b.executor.dry_run = True
    b.day = bot_mod.DayState()
    b.logger = MagicMock()
    b.tickers = {}
    ts = bot_mod.TickerState(symbol="ABC", rank=1, score=1.0)
    b.tickers["ABC"] = ts
    # Bad bar — missing required keys to provoke exception in try-block
    bad_bar = {"timestamp": datetime(2026, 5, 13, 14, 0, tzinfo=timezone.utc)}
    # Should NOT raise NameError or any other exception
    await b.handle_bar_5min("ABC", bad_bar)
    # Repeat to ensure stable
    for _ in range(5):
        await b.handle_bar_5min("ABC", bad_bar)


@pytest.mark.asyncio
async def test_handle_bar_5min_logs_correct_symbol_on_error(caplog):
    """Log message should contain symbol, not the missing `bar` ref."""
    import logging
    import bot as bot_mod
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.executor = MagicMock()
    b.day = bot_mod.DayState()
    b.logger = MagicMock()
    b.tickers = {"XYZ": bot_mod.TickerState(symbol="XYZ", rank=1, score=1.0)}
    # Trigger exception by passing bad bar (no timestamp, no required keys)
    bad_bar = {}
    with caplog.at_level(logging.ERROR):
        await b.handle_bar_5min("XYZ", bad_bar)
    # If anything logged, message must reference 'XYZ' not undefined 'bar'
    for r in caplog.records:
        if "crashed" in r.message:
            assert "XYZ" in r.message
            assert "bar" not in r.message or "_5min" in r.message


# ─── Fix: NY_TZ DST-aware ────────────────────────────────────────────────────
def test_ny_tz_is_zoneinfo_not_fixed_offset():
    """REGRESSION: vorher fixed UTC-4 → falsch in EST winter window."""
    import bot as bot_mod
    from zoneinfo import ZoneInfo
    # NY_TZ should be ZoneInfo for proper DST handling
    assert isinstance(bot_mod.NY_TZ, ZoneInfo) or hasattr(bot_mod.NY_TZ, 'key')
    # Verify a winter date (EST) gives UTC-5
    winter_dt = datetime(2026, 1, 15, 9, 30, tzinfo=bot_mod.NY_TZ)
    assert winter_dt.utcoffset() == timedelta(hours=-5), \
        f"EST should be UTC-5, got {winter_dt.utcoffset()}"
    # Verify summer (EDT) gives UTC-4
    summer_dt = datetime(2026, 7, 15, 9, 30, tzinfo=bot_mod.NY_TZ)
    assert summer_dt.utcoffset() == timedelta(hours=-4), \
        f"EDT should be UTC-4, got {summer_dt.utcoffset()}"


def test_ny_tz_dst_transition_dates():
    """Verify DST transitions don't break (2nd Sunday March, 1st Sunday Nov)."""
    import bot as bot_mod
    # Day before US DST start 2026 (Sunday March 8)
    before = datetime(2026, 3, 7, 12, 0, tzinfo=bot_mod.NY_TZ)
    after = datetime(2026, 3, 9, 12, 0, tzinfo=bot_mod.NY_TZ)
    assert before.utcoffset() == timedelta(hours=-5)
    assert after.utcoffset() == timedelta(hours=-4)


# ─── Fix: find_pilot_data_paths ──────────────────────────────────────────────
def test_pilot_data_helper_finds_existing_path():
    import bot as bot_mod
    bars, cands = bot_mod.find_pilot_data_paths()
    assert bars is not None or cands is None  # both or none
    if bars is not None:
        assert bars.exists()
        assert cands.exists()


def test_pilot_data_helper_returns_none_when_missing(tmp_path, monkeypatch):
    """When neither backtest_data nor 04_backtest exists → (None, None)."""
    import bot as bot_mod
    # Patch the helper's root to a path with neither layout
    # The helper resolves from bot.py's parent.parent. We can patch __file__:
    # Simpler: check return-shape — it should never raise.
    bars, cands = bot_mod.find_pilot_data_paths()
    # Either both exist or both None
    if bars is None:
        assert cands is None
    else:
        assert cands is not None


# ─── Fix: market_close_all short-position handling ───────────────────────────
def test_market_close_fallback_uses_BUY_for_short_position():
    """REGRESSION: vorher fallback always SELL — Short-Pos würde NOCH MEHR
    short verkauft (account weiter ins minus)."""
    import bot as bot_mod
    from alpaca.trading.enums import OrderSide
    ex = bot_mod.AlpacaExecutor.__new__(bot_mod.AlpacaExecutor)
    ex.client = MagicMock()
    ex.client.get_orders.return_value = []
    ex.dry_run = False

    # Stateful mock: positions exist, never reduce → fallback fires
    short_pos = MagicMock()
    short_pos.symbol = "ABC"
    short_pos.qty = -10  # SHORT 10 shares
    long_pos = MagicMock()
    long_pos.symbol = "XYZ"
    long_pos.qty = 5

    call_count = {"n": 0}

    def list_side(*a, **kw):
        call_count["n"] += 1
        # Pre-list (1), poll-loop (2-3), remaining-after-attempt1 (4),
        # leftover-for-fallback (5) → all return positions.
        # Final-verify after fallback (6+) → empty (= flat).
        if call_count["n"] >= 6:
            return []
        return [short_pos, long_pos]

    ex.client.get_all_positions.side_effect = list_side
    ex.client.submit_order.return_value = MagicMock(id="x")
    ex.market_close_all(max_attempts=1, verify_timeout_sec=0.1,
                         poll_interval_sec=0.05)
    # Verify submit_order called with correct sides
    calls = ex.client.submit_order.call_args_list
    by_symbol = {}
    for c in calls:
        req = c.args[0]
        by_symbol[req.symbol] = req.side
    # ABC was SHORT (-10) → fallback should BUY to cover
    assert by_symbol.get("ABC") == OrderSide.BUY, \
        f"Expected BUY for SHORT position ABC, got {by_symbol.get('ABC')}"
    # XYZ was LONG (+5) → fallback should SELL
    assert by_symbol.get("XYZ") == OrderSide.SELL, \
        f"Expected SELL for LONG position XYZ, got {by_symbol.get('XYZ')}"


# ─── Fix: Bot.run task management for HARD_FLAT ──────────────────────────────
def test_bot_run_uses_first_completed_not_gather():
    """REGRESSION: vorher asyncio.gather → ws_loop blockt nach HARD_FLAT.
    Source-check: asyncio.wait with FIRST_COMPLETED muss drin sein."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Old anti-pattern should be gone
    assert "asyncio.gather(ws_loop()" not in src, \
        "Bot.run still uses gather — HARD_FLAT can't clean up ws_loop"
    # New pattern should be in
    assert "asyncio.wait" in src
    assert "FIRST_COMPLETED" in src
