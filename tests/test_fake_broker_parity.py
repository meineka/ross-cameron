"""Review-V2 P2.x: FakeBroker golden-scenario parity tests.

These tests validate that the Bot's INTERNAL state (ts.in_position,
ts.shares) stays in lockstep with the BROKER's truth (FakeBroker.positions)
across the order-lifecycle edge cases the reviewer flagged.

Each scenario:
  1. Sets up a FakeBroker with a specific behavior
  2. Drives Bot.manage_position with a bar that triggers exit/add
  3. Asserts BOTH:
     - bot.tickers[sym].shares == fake_broker.positions[sym]
     - bot.tickers[sym].in_position matches has-shares state
     - day.realized_pnl matches actual-fill-price math
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bot_with_fake_broker(symbol="AAA", entry=10.0, target1=10.5,
                           target2=11.0, stop=9.5, initial=100,
                           half_filled=False):
    """Bot wired with FakeBroker pre-loaded with the position."""
    import bot as bot_mod
    from fake_broker import FakeBroker

    fb = FakeBroker(default_behavior="filled_at_limit")
    fb.positions[symbol] = initial // (2 if half_filled else 1)
    fb.avg_prices[symbol] = entry

    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.executor = fb
    b.day = bot_mod.DayState()
    b.day.realized_pnl = 0.0
    b.day.consecutive_losses = 0
    b.logger = MagicMock()
    b.tickers = {}
    ts = bot_mod.TickerState(symbol=symbol, rank=1, score=1.0)
    ts.in_position = True
    ts.entry_price = entry
    ts.stop_price = stop
    ts.target1_price = target1
    ts.target2_price = target2
    ts.initial_shares = initial
    ts.bars_since_entry = 5
    ts.bars = []
    ts.last_add_price = entry + 100  # very high → Add never triggers in this test
    ts.adds_count = 999  # belt+suspenders: disable adds via cap
    if half_filled:
        ts.half_filled = True
        ts.shares = initial // 2
        ts.t1_shares_sold = initial // 2
    else:
        ts.half_filled = False
        ts.shares = initial
        ts.t1_shares_sold = 0
    b.tickers[symbol] = ts
    return b, ts, fb


# ─── Scenario 1: clean T1 → T2 win ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_clean_t1_t2_win_keeps_parity():
    """Bull-flag wins T1 partial → next bar T2 full. Bot and broker stay
    synchronized at every step."""
    b, ts, fb = _bot_with_fake_broker(initial=10)
    # T1 hit: bar.high crosses target1=10.5
    bar_t1 = {"open": 10.40, "high": 10.55, "low": 10.35, "close": 10.50,
              "volume": 1000}
    await b.manage_position(ts, bar_t1, None)
    # After T1: bot sold 5 (half), 5 remain. Broker also has 5.
    assert ts.half_filled is True
    assert ts.shares == 5
    assert ts.t1_shares_sold == 5
    assert fb.positions["AAA"] == 5  # broker truth
    assert ts.in_position is True

    # T2 hit: bar.high crosses target2=11.0
    bar_t2 = {"open": 10.95, "high": 11.05, "low": 10.90, "close": 11.00,
              "volume": 1000}
    await b.manage_position(ts, bar_t2, None)
    assert ts.in_position is False
    assert fb.positions["AAA"] == 0  # broker fully flat
    # PnL: T1: (10.5-10)*5 = 2.5, T2: (11-10)*5 = 5.0, total = 7.5
    assert abs(b.day.realized_pnl - 7.5) < 1e-6


# ─── Scenario 2: stop-out clean ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_clean_stop_out_keeps_parity():
    """No T1, stop hit immediately. Bot books $50 loss, broker flat."""
    b, ts, fb = _bot_with_fake_broker(initial=10, stop=9.5)
    bar_stop = {"open": 9.55, "high": 9.55, "low": 9.40, "close": 9.50,
                "volume": 1000}
    await b.manage_position(ts, bar_stop, None)
    assert ts.in_position is False
    assert fb.positions["AAA"] == 0
    # Stop sells at (stop - slippage). Loss = (9.5-0.05-10)*10 = -5.50
    import bot as _bm
    expected = (9.5 - _bm.SLIPPAGE_CENTS - 10.0) * 10
    assert abs(b.day.realized_pnl - expected) < 1e-6


# ─── Scenario 3: T1 limit REJECTED — bot stays in_position ───────────────────
@pytest.mark.asyncio
async def test_t1_rejected_bot_stays_in_position():
    """The CRITICAL parity bug Reviewer-V2 flagged: if T1-limit-sell rejected,
    bot must NOT think it's half-filled. Position stays full at broker AND bot."""
    b, ts, fb = _bot_with_fake_broker(initial=10)
    fb.set_behavior("AAA", "rejected")  # T1 will be rejected
    bar_t1 = {"open": 10.40, "high": 10.55, "low": 10.35, "close": 10.50,
              "volume": 1000}
    await b.manage_position(ts, bar_t1, None)
    # Rejection: bot must stay in_position with FULL shares
    assert ts.in_position is True
    assert ts.shares == 10  # NOT decremented
    assert ts.half_filled is False
    assert fb.positions["AAA"] == 10  # broker still long
    assert b.day.realized_pnl == 0.0  # NO PnL booked on rejection


# ─── Scenario 4: stop limit TIMEOUT → market fallback fires ──────────────────
@pytest.mark.asyncio
async def test_stop_timeout_market_fallback_exits():
    """When stop-exit limit times out, market_fallback fires, position flat."""
    b, ts, fb = _bot_with_fake_broker(initial=10, stop=9.5)
    fb.set_behavior("AAA", "timeout", market_slip_cents=0.10)
    bar_stop = {"open": 9.55, "high": 9.55, "low": 9.40, "close": 9.50,
                "volume": 1000}
    await b.manage_position(ts, bar_stop, None)
    assert ts.in_position is False
    assert fb.positions["AAA"] == 0  # market-fallback succeeded
    # Position closed at market price (slip extra 0.10c below limit-price)


# ─── Scenario 5: T2 partial fill — bot reduces shares but stays in_position ─
@pytest.mark.asyncio
async def test_t2_partial_keeps_remainder():
    """T2 limit partial-fills 5 of 10. Bot has 5 left, broker has 5."""
    b, ts, fb = _bot_with_fake_broker(initial=10, target2=11.0)
    fb.set_behavior("AAA", "partial", partial_qty=5)
    # bar that crosses T2
    bar_t2 = {"open": 10.95, "high": 11.05, "low": 10.90, "close": 11.00,
              "volume": 1000}
    await b.manage_position(ts, bar_t2, None)
    # Bot must NOT mark flat — 5 remain
    assert ts.in_position is True
    assert ts.shares == 5
    assert fb.positions["AAA"] == 5


# ─── Scenario 6: Pyramid-Add rejected — main position unchanged ─────────────
@pytest.mark.asyncio
async def test_pyramid_add_rejected_no_state_change():
    """Add-order rejected. ts.shares and ts.entry_price MUST NOT change."""
    b, ts, fb = _bot_with_fake_broker(initial=100)
    # Override the disabled-adds setup so the add-block IS exercised
    ts.last_add_price = 10.0
    ts.adds_count = 0
    # Add-trigger fires when bar.high >= last_add_price + ADD_TRIGGER_CENTS
    # and bar.close > entry. Make Add fail.
    fb.set_behavior("AAA", "rejected")
    bar_add = {"open": 10.30, "high": 10.40, "low": 10.25, "close": 10.35,
               "volume": 1000}
    pre_shares = ts.shares
    pre_avg = ts.entry_price
    pre_adds = ts.adds_count
    ts.bars = [{"close": 10.0 + i*0.01} for i in range(35)]
    with patch("bot.macd_bear_cross", return_value=False):
        await b.manage_position(ts, bar_add, None)
    # State must be unchanged — add was rejected
    assert ts.shares == pre_shares
    assert ts.entry_price == pre_avg
    assert ts.adds_count == pre_adds


# ─── Scenario 7: broker-truth assertion helpers work ─────────────────────────
def test_fake_broker_basic_api():
    """Smoke: FakeBroker exposes the methods Bot needs."""
    from fake_broker import FakeBroker
    fb = FakeBroker()
    assert callable(getattr(fb, "submit_bracket_buy"))
    assert callable(getattr(fb, "submit_sell_with_confirm"))
    assert callable(getattr(fb, "submit_buy_with_confirm"))
    assert callable(getattr(fb, "verify_and_repair_protection"))
    assert callable(getattr(fb, "protect_position"))
    assert callable(getattr(fb, "cancel_open_orders_for"))
    assert callable(getattr(fb, "get_equity"))
    assert fb.get_equity() == 25_000.0
    assert fb.is_flat("XYZ") is True
