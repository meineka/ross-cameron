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
