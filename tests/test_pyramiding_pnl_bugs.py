"""Audit-Iter 12 (2026-05-12): Pyramiding (Add-to-Winner) PnL-Buchhaltung.

Bug PYR-1 (HIGH): bei Pyramiding stimmten die T1-Realisations-Shares in
  PnL-Math nicht. Code nutzte `ts.initial_shares * 0.5` aber T1 verkaufte
  tatsächlich `(initial + adds) // 2`. Folge: bei jedem multi-add → T1 → T2
  Trade fehlten die Pyramid-Add-Gewinne in den realized_pnl-Einträgen.

  Beispiel: 100 sh @ $10 (initial) + 25 sh @ $10.10 + 25 sh @ $10.20
                                     + 25 sh @ $10.30 = 175 total
            T1 @ $10.50 verkauft 175//2 = 87 sh
            Code-Vorher: r1 = (10.50 - new_avg) * 50  (50 = initial * 0.5)
            Korrekt:     r1 = (10.50 - new_avg) * 87
            Diff:       37 sh * gain = signifikant in $-PnL

Fix: neues Feld `ts.t1_shares_sold` capturet die EXAKTE Menge beim T1.
     Stop-Exit + MACD-Exit + T2-Exit nutzen jetzt das statt initial*0.5.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_bot_with_position(entry=10.0, target1=10.5, target2=11.0,
                              stop=9.5, initial=100):
    """Standard-Bot-Setup für manage_position-Tests.
    Review-V2 P0: configure dict-returning confirm-methods."""
    import bot as bot_mod
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.executor = MagicMock()
    b.executor.dry_run = False
    def _sell_confirm(sym, shares, price, reason, **kwargs):
        return {"status": "filled", "filled_qty": shares,
                "avg_fill_price": price, "order_id": f"mock-{sym}"}
    def _buy_confirm(sym, shares, price, **kwargs):
        return {"status": "filled", "filled_qty": shares,
                "avg_fill_price": price, "order_id": f"mock-{sym}"}
    b.executor.submit_sell_with_confirm.side_effect = _sell_confirm
    b.executor.submit_buy_with_confirm.side_effect = _buy_confirm
    b.day = bot_mod.DayState()
    b.day.realized_pnl = 0.0
    b.day.consecutive_losses = 0
    b.logger = MagicMock()
    b.tickers = {}
    ts = bot_mod.TickerState(symbol="X", rank=1, score=1.0)
    ts.in_position = True
    ts.entry_price = entry
    ts.stop_price = stop
    ts.target1_price = target1
    ts.target2_price = target2
    ts.initial_shares = initial
    ts.shares = initial
    ts.last_add_price = entry
    ts.half_filled = False
    ts.t1_shares_sold = 0
    ts.bars_since_entry = 10  # past quick-exit window
    ts.bars = []
    b.tickers["X"] = ts
    return b, ts


# ─── Bug PYR-1: T1 with pyramid adds, then T2 ────────────────────────────────
@pytest.mark.asyncio
async def test_t2_pnl_correct_after_pyramid_then_t1():
    """REGRESSION: 100 initial + 3x25 adds = 175 sh. T1 sells 87 sh.
    T2 sells remaining 88 sh. PnL-Math muss t1_shares_sold=87 nutzen.
    """
    import bot as bot_mod
    b, ts = _make_bot_with_position(entry=10.0, target1=10.5, target2=11.0,
                                      initial=100)
    # Simuliere 3 pyramid adds:
    add_size = max(1, int(100 * bot_mod.ADD_FRACTION))  # = 25
    # Add #1 @ +10c: new_avg = (100*10 + 25*10.10) / 125
    old_avg = ts.entry_price
    ts.entry_price = (100 * 10.0 + 25 * 10.10) / 125
    ts.shares = 125
    ts.adds_count = 1
    ts.last_add_price = 10.10
    # Add #2 @ +20c: new_avg
    ts.entry_price = (125 * ts.entry_price + 25 * 10.20) / 150
    ts.shares = 150
    ts.adds_count = 2
    ts.last_add_price = 10.20
    # Add #3 @ +30c
    ts.entry_price = (150 * ts.entry_price + 25 * 10.30) / 175
    ts.shares = 175
    ts.adds_count = 3
    ts.last_add_price = 10.30
    new_avg = ts.entry_price

    # T1-Bar (high >= 10.50)
    bar_t1 = {"open": 10.45, "high": 10.55, "low": 10.45, "close": 10.50, "volume": 1000}
    ts.bars = [{"close": 10.0 + i * 0.005} for i in range(20)]  # <30 → MACD off
    await b.manage_position(ts, bar_t1, None)
    assert ts.half_filled is True
    assert ts.t1_shares_sold == 87  # 175 // 2
    assert ts.shares == 88           # 175 - 87

    # T2-Bar (high >= 11.00)
    bar_t2 = {"open": 10.95, "high": 11.05, "low": 10.95, "close": 11.00, "volume": 1000}
    await b.manage_position(ts, bar_t2, None)
    # PnL korrekt = (10.50 - new_avg) * 87  +  (11.00 - new_avg) * 88
    expected_r1 = (10.50 - new_avg) * 87
    expected_r2 = (11.00 - new_avg) * 88
    expected_pnl = expected_r1 + expected_r2
    assert abs(b.day.realized_pnl - expected_pnl) < 0.01, \
        f"expected ${expected_pnl:.4f}, got ${b.day.realized_pnl:.4f}"


# ─── Sanity: ohne Pyramiding, t1_shares_sold == initial // 2 ─────────────────
@pytest.mark.asyncio
async def test_t1_shares_sold_equals_half_initial_without_adds():
    """Ohne Pyramid: t1_shares_sold = initial // 2 (Backwards-Kompat).
    Pyramid via adds_count = MAX gesperrt für diesen Test."""
    import bot as bot_mod
    b, ts = _make_bot_with_position(entry=10.0, target1=10.5, initial=10)
    ts.adds_count = bot_mod.MAX_ADDS_PER_TRADE  # pyramid lock
    bar_t1 = {"open": 10.45, "high": 10.55, "low": 10.45, "close": 10.50, "volume": 1000}
    ts.bars = [{"close": 10.0} for _ in range(20)]
    await b.manage_position(ts, bar_t1, None)
    assert ts.t1_shares_sold == 5
    assert ts.shares == 5
    assert ts.half_filled is True


# ─── Stop-Exit nach Pyramid + T1 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stop_exit_pnl_correct_after_pyramid_t1():
    """Pyramid → T1 → stop hit (BE). Stop-Exit-PnL muss t1_shares_sold nutzen."""
    b, ts = _make_bot_with_position(entry=10.0, target1=10.5, target2=11.0,
                                      stop=9.5, initial=100)
    # Pyramid auf 175 sh, avg = 10.0857
    ts.shares = 175
    ts.entry_price = 10.0857
    new_avg = ts.entry_price
    # T1 setzen
    ts.half_filled = True
    ts.t1_shares_sold = 87
    ts.shares = 88
    # Stop = entry_price (BE-Move nach T1). Sell at (stop - SLIPPAGE).
    import bot as _bm
    bar_stop = {"open": 10.10, "high": 10.10, "low": 10.0, "close": 10.05, "volume": 1000}
    ts.bars = [{"close": 10.0} for _ in range(5)]
    await b.manage_position(ts, bar_stop, None)
    actual_sell = new_avg - _bm.SLIPPAGE_CENTS
    expected = (actual_sell - new_avg) * 88 + (10.5 - new_avg) * 87
    assert abs(b.day.realized_pnl - expected) < 0.01


# ─── MACD-Exit nach Pyramid + T1 ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_macd_exit_pnl_correct_after_pyramid_t1():
    """Pyramid → T1 → MACD bear-cross. MACD-Exit-PnL korrekt."""
    b, ts = _make_bot_with_position(entry=10.0, target1=10.5, target2=11.0,
                                      initial=100)
    ts.shares = 175
    ts.entry_price = 10.0857
    new_avg = ts.entry_price
    ts.half_filled = True
    ts.t1_shares_sold = 87
    ts.shares = 88
    # MACD-Exit bei $10.30
    bar = {"open": 10.32, "high": 10.34, "low": 10.28, "close": 10.30, "volume": 1000}
    ts.bars = [{"close": 10.0 + i * 0.01} for i in range(35)]
    import bot as _bm
    with patch("bot.macd_bear_cross", return_value=True):
        await b.manage_position(ts, bar, None)
    actual_sell = 10.30 - _bm.SLIPPAGE_CENTS
    expected = (actual_sell - new_avg) * 88 + (10.50 - new_avg) * 87
    assert abs(b.day.realized_pnl - expected) < 0.01


# ─── TickerState-Initialization ──────────────────────────────────────────────
def test_ticker_state_initializes_t1_shares_sold_to_zero():
    """Neues Feld muss default 0 sein, sonst krieg stop-exit-pre-T1 falsch."""
    import bot as bot_mod
    ts = bot_mod.TickerState(symbol="X", rank=1, score=1.0)
    assert ts.t1_shares_sold == 0


# ─── Stop-Exit OHNE T1: pnl muss nichts adden ────────────────────────────────
@pytest.mark.asyncio
async def test_stop_exit_pnl_without_t1_no_t1_addition():
    """Wenn half_filled=False, darf KEIN T1-PnL addiert werden auch wenn
    t1_shares_sold (theoretisch noch 0) gefragt würde."""
    b, ts = _make_bot_with_position(entry=10.0, stop=9.5, initial=10)
    ts.half_filled = False
    ts.t1_shares_sold = 0
    bar = {"open": 9.50, "high": 9.55, "low": 9.40, "close": 9.50, "volume": 1000}
    ts.bars = [{"close": 10.0} for _ in range(5)]
    await b.manage_position(ts, bar, None)
    import bot as _bm
    actual_sell = 9.5 - _bm.SLIPPAGE_CENTS
    expected = (actual_sell - 10.0) * 10  # only initial position loss
    assert abs(b.day.realized_pnl - expected) < 0.01
