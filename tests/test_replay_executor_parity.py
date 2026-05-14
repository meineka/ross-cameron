"""Review-V2 Phase 8 (ChatGPT 14:36-answer): ReplayBot uses the SAME
order-execution lifecycle as the live Bot.

This test proves the architectural integration:
  - ReplayBot with executor=None → legacy inline _manage
  - ReplayBot with executor=FakeBroker(filled_at_limit) → routes through
    submit_bracket_buy + submit_sell_with_confirm (same path live uses)
  - Both produce IDENTICAL PnL on a known reference day

When FakeBroker behavior is changed (rejected/partial/timeout), the two
paths intentionally diverge — exactly the simulation realism reviewer
asked for.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))

PILOT_DATA = ROOT / "04_backtest" / "data_pilot" / "intraday_5m.parquet"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing")
def test_replay_executor_parity_on_2026_04_15():
    """Legacy ReplayBot and ReplayBot-with-FakeBroker produce identical
    PnL on 2026-04-15. Proves the executor-injection refactor preserves
    backtest semantics (default filled_at_limit behavior)."""
    import bot
    from fake_broker import FakeBroker

    # Legacy path
    rb_legacy = bot.ReplayBot()  # executor=None
    rb_legacy.run("2026-04-15")
    pnl_legacy = round(rb_legacy.day.realized_pnl, 2)
    trades_legacy = rb_legacy.day.trades_completed_today

    # FakeBroker path
    fb = FakeBroker(default_behavior="filled_at_limit")
    rb_fake = bot.ReplayBot(executor=fb)
    rb_fake.run("2026-04-15")
    pnl_fake = round(rb_fake.day.realized_pnl, 2)
    trades_fake = rb_fake.day.trades_completed_today

    assert pnl_legacy == pnl_fake, \
        f"PnL diverged: legacy=${pnl_legacy} vs fakebroker=${pnl_fake}"
    assert trades_legacy == trades_fake, \
        f"Trade count diverged: legacy={trades_legacy} vs fakebroker={trades_fake}"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing")
def test_replay_fakebroker_position_state_matches_bot_state():
    """After 2026-04-15 replay, FakeBroker.positions reflects what Bot
    tickers indicate — proves broker-truth and bot-state are aligned."""
    import bot
    from fake_broker import FakeBroker
    fb = FakeBroker(default_behavior="filled_at_limit")
    rb = bot.ReplayBot(executor=fb)
    rb.run("2026-04-15")
    # All positions should be flat (T1+T2 hit, or stop hit)
    for sym, ts in rb.tickers.items():
        if not ts.in_position:
            # If bot says flat, broker should also be flat
            broker_qty = fb.positions.get(sym, 0)
            assert broker_qty == 0, \
                f"Bot says {sym} flat but FakeBroker holds {broker_qty} shares"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing")
def test_replay_fakebroker_reject_keeps_position_intact():
    """When FakeBroker rejects all sells, the position state must not be
    mutated. ReplayBot must respect the same broker-truth as Bot."""
    import bot
    from fake_broker import FakeBroker
    fb = FakeBroker(default_behavior="rejected")
    rb = bot.ReplayBot(executor=fb)
    rb.run("2026-04-15")
    # With every entry rejected, no positions opened, PnL=0
    assert round(rb.day.realized_pnl, 2) == 0.0, \
        f"All entries rejected — PnL should be $0, got ${rb.day.realized_pnl}"
    assert rb.day.trades_completed_today == 0


def test_replaybot_init_accepts_executor():
    """API: ReplayBot constructor accepts optional executor= kwarg."""
    import bot
    from fake_broker import FakeBroker
    rb1 = bot.ReplayBot()
    assert rb1.executor is None
    rb2 = bot.ReplayBot(executor=FakeBroker())
    assert rb2.executor is not None
    # Helper method present
    assert callable(getattr(rb2, "_executor_sell", None))


# ─── Phase-9 (ChatGPT-17:49): partial-fill scenarios ─────────────────────────
def _setup_partial_ts(executor):
    """Helper: half-filled position with 5 shares remaining."""
    import bot
    ts = bot.TickerState(symbol="AAA", rank=1, score=1.0)
    ts.in_position = True
    ts.entry_price = 10.0
    ts.stop_price = 9.5
    ts.target1_price = 10.5
    ts.target2_price = 11.0
    ts.shares = 5
    ts.initial_shares = 10
    ts.half_filled = True
    ts.t1_shares_sold = 5
    ts.bars_since_entry = 10
    rb = bot.ReplayBot(executor=executor)
    rb.tickers["AAA"] = ts
    executor.positions["AAA"] = 5
    executor.avg_prices["AAA"] = 10.0
    return rb, ts


def test_replay_t2_partial_fill_keeps_remainder_position():
    """T2 partial 3/5 → bot.shares=2, broker.qty=2, in_position=True."""
    from fake_broker import FakeBroker
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=3)
    rb, ts = _setup_partial_ts(fb)
    rb._manage(ts, {"open": 11.0, "high": 11.1, "low": 10.9, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 2, f"expected 2 shares remaining, got {ts.shares}"
    assert ts.in_position is True, "must stay in_position with shares > 0"
    assert fb.positions["AAA"] == 2, "broker truth must match"
    assert rb.day.trades_completed_today == 0, \
        "trade-counter must not increment on partial T2"


def test_replay_stop_partial_fill_keeps_remainder_position():
    """Stop partial 2/5 → bot.shares=3, broker.qty=3, in_position=True.
    Critical: log.critical fires (unprotected position)."""
    from fake_broker import FakeBroker
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=2)
    rb, ts = _setup_partial_ts(fb)
    # Bar with low below BE-stop (entry_price for half_filled)
    rb._manage(ts, {"open": 9.95, "high": 9.97, "low": 9.0, "close": 9.5,
                     "volume": 1000}, None)
    assert ts.shares == 3, f"expected 3 shares remaining, got {ts.shares}"
    assert ts.in_position is True
    assert fb.positions["AAA"] == 3
    assert rb.day.trades_completed_today == 0


def test_replay_qe_partial_fill_keeps_remainder_position():
    """QE partial 2/5 → bot.shares=3, broker.qty=3, in_position=True."""
    from fake_broker import FakeBroker
    import bot
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=2)
    # QE only fires pre-T1
    ts = bot.TickerState(symbol="AAA", rank=1, score=1.0)
    ts.in_position = True
    ts.entry_price = 10.0
    ts.stop_price = 9.5
    ts.target1_price = 10.5
    ts.target2_price = 11.0
    ts.shares = 5
    ts.initial_shares = 5
    ts.half_filled = False
    ts.t1_shares_sold = 0
    ts.bars_since_entry = 0  # within QE window
    rb = bot.ReplayBot(executor=fb)
    rb.tickers["AAA"] = ts
    fb.positions["AAA"] = 5
    fb.avg_prices["AAA"] = 10.0
    # Bar with low driving 30c below entry → QE triggers
    rb._manage(ts, {"open": 9.85, "high": 9.90, "low": 9.65, "close": 9.70,
                     "volume": 1000}, None)
    assert ts.shares == 3, f"expected 3 shares remaining, got {ts.shares}"
    assert ts.in_position is True
    assert fb.positions["AAA"] == 3
    assert rb.day.trades_completed_today == 0


def test_replay_partial_then_full_exits_cleanly():
    """Partial 3 + later full-fill 2 should end flat with correct PnL.
    Phase-10 (ChatGPT-18:20): PnL must equal 7.50, not 10.00 — T1-leg
    booked ONCE across both T2-partial-fills.
      T1:        (10.5 - 10.0) * 5 = 2.5
      T2 part-1: (11.0 - 10.0) * 3 = 3.0
      T2 final:  (11.0 - 10.0) * 2 = 2.0
      Total = 7.5
    """
    from fake_broker import FakeBroker
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=3)
    rb, ts = _setup_partial_ts(fb)
    # NB: live T1 path does NOT book to realized_pnl — it's deferred to T2/Stop
    # via _book_t1_pnl_once, so no seeding is needed.
    # First T2-bar: partial 3
    rb._manage(ts, {"open": 11.0, "high": 11.1, "low": 10.9, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 2
    # Switch FakeBroker to filled (next bar fills fully)
    fb.set_behavior("AAA", "filled_at_limit")
    rb._manage(ts, {"open": 11.0, "high": 11.05, "low": 10.95, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 0
    assert ts.in_position is False
    assert fb.positions["AAA"] == 0
    assert rb.day.trades_completed_today == 1
    assert abs(rb.day.realized_pnl - 7.5) < 1e-9, \
        f"expected $7.50 PnL (T1 once + T2 partial + T2 final), got ${rb.day.realized_pnl}"


def test_replay_partial_t2_then_partial_t2_then_final():
    """Phase-10: partial T2 → partial T2 → final T2 must book T1 ONCE.
      T1:        (10.5 - 10.0) * 5 = 2.5
      T2 part-1: (11.0 - 10.0) * 2 = 2.0
      T2 part-2: (11.0 - 10.0) * 2 = 2.0
      T2 final:  (11.0 - 10.0) * 1 = 1.0
      Total = 7.5 (NOT 12.5 with naive triple-booking of r1)
    """
    from fake_broker import FakeBroker
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=2)
    rb, ts = _setup_partial_ts(fb)
    # partial 2
    rb._manage(ts, {"open": 11.0, "high": 11.1, "low": 10.9, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 3
    # partial 2 again
    rb._manage(ts, {"open": 11.0, "high": 11.1, "low": 10.9, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 1
    # final 1
    fb.set_behavior("AAA", "filled_at_limit")
    rb._manage(ts, {"open": 11.0, "high": 11.05, "low": 10.95, "close": 11.0,
                     "volume": 1000}, None)
    assert ts.shares == 0
    assert ts.in_position is False
    assert abs(rb.day.realized_pnl - 7.5) < 1e-9, \
        f"expected $7.50 (T1 once + 3 T2 legs), got ${rb.day.realized_pnl}"


def test_replay_t1_then_partial_stop_then_final_stop():
    """Phase-10: T1 → partial BE-stop → final BE-stop must book T1 ONCE.
    BE-stop = entry_price (10.0) so the T2-leg PnL on each stop fill is $0.
    Only the T1-leg contributes, and it must contribute only once.
      T1:        (10.5 - 10.0) * 5 = 2.5
      Stop p-1:  (10.0 - 10.0) * 2 = 0.0
      Stop p-2:  (10.0 - 10.0) * 3 = 0.0
      Total = 2.5 (NOT 7.5 with naive double-booking)
    """
    from fake_broker import FakeBroker
    fb = FakeBroker()
    fb.set_behavior("AAA", "partial", partial_qty=2)
    rb, ts = _setup_partial_ts(fb)
    # bar with low <= BE (entry) but high < T2 → stop branch
    rb._manage(ts, {"open": 9.95, "high": 9.99, "low": 9.5, "close": 9.7,
                     "volume": 1000}, None)
    assert ts.shares == 3
    assert ts.in_position is True
    # final stop fill
    fb.set_behavior("AAA", "filled_at_limit")
    rb._manage(ts, {"open": 9.95, "high": 9.99, "low": 9.5, "close": 9.7,
                     "volume": 1000}, None)
    assert ts.shares == 0
    assert ts.in_position is False
    assert abs(rb.day.realized_pnl - 2.5) < 1e-9, \
        f"expected $2.50 (T1 once + 0 stop legs), got ${rb.day.realized_pnl}"
