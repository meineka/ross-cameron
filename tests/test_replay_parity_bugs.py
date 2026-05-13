"""Audit-Iter 19 (2026-05-12): ReplayBot ↔ live Bot PnL-Parität.

ReplayBot ist die Backtest-Validierung. Wenn die PnL-Math vom live Bot
abweicht, ist die Baseline statistisch zufällig statt aussagekräftig.

Bugs gefunden:
  REP-1 (HIGH): T2-Exit zählte T1-Gewinn nicht (mirror live MP-1/PYR-1)
  REP-2 (HIGH): Stop-after-T1 zählte T1-Gewinn nicht
  REP-5 (MED):  trades_completed_today nie incremented → MAX_TRADES_PER_DAY
               griff in Replay nicht
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_replay_with_position(entry=10.0, target1=10.5, target2=11.0,
                                 stop=9.5, initial=10, half_filled=False):
    import bot as bot_mod
    r = bot_mod.ReplayBot.__new__(bot_mod.ReplayBot)
    r.day = bot_mod.DayState()
    r.logger = MagicMock()
    r.equity = 25_000.0
    r.tickers = {}
    ts = bot_mod.TickerState(symbol="X", rank=1, score=1.0)
    ts.in_position = True
    ts.entry_price = entry
    ts.stop_price = stop
    ts.target1_price = target1
    ts.target2_price = target2
    ts.initial_shares = initial
    ts.shares = initial
    ts.t1_shares_sold = 0
    if half_filled:
        ts.half_filled = True
        ts.t1_shares_sold = initial // 2
        ts.shares = initial - ts.t1_shares_sold
    else:
        ts.half_filled = False
    ts.bars = []
    r.tickers["X"] = ts
    return r, ts


# ─── REP-1: T2-Exit zählt T1-Gewinn ─────────────────────────────────────────
def test_replay_t2_exit_after_t1_includes_t1_pnl():
    """REGRESSION: 10 sh @ $10. T1 @ $10.50 → 5 sh sold. T2 @ $11.00 → 5 sh.
    Vorher: pnl = (11.00 - 10.00) * 5 = $5.00. Korrekt: $5.00 + $2.50 = $7.50."""
    r, ts = _make_replay_with_position(half_filled=True)
    r.submit_sell = MagicMock()
    bar = {"open": 10.99, "high": 11.05, "low": 10.95, "close": 11.0, "volume": 1000}
    r._manage(ts, bar, None)
    expected = (10.50 - 10.0) * 5 + (11.0 - 10.0) * 5
    assert abs(r.day.realized_pnl - expected) < 0.01


# ─── REP-2: Stop-Exit-after-T1 ──────────────────────────────────────────────
def test_replay_stop_after_t1_includes_t1_pnl():
    """T1 hit, dann stop (=BE) hit → PnL = T1-Gewinn + 0 (stop=entry).
    Vorher: pnl = 0. Korrekt: $2.50."""
    r, ts = _make_replay_with_position(half_filled=True)
    r.submit_sell = MagicMock()
    # bar.low <= stop (=entry_price=10.0)
    bar = {"open": 10.05, "high": 10.05, "low": 9.95, "close": 10.0, "volume": 1000}
    r._manage(ts, bar, None)
    expected = (10.0 - 10.0) * 5 + (10.50 - 10.0) * 5  # BE-stop + T1-gain
    assert abs(r.day.realized_pnl - expected) < 0.01


# ─── REP-1 + T1-only-bar (T1 erst, T2 später) ────────────────────────────────
def test_replay_t1_sets_t1_shares_sold():
    """Beim T1 wird t1_shares_sold gesetzt (für späteren T2 oder stop)."""
    r, ts = _make_replay_with_position(half_filled=False, initial=10)
    r.submit_sell = MagicMock()
    bar = {"open": 10.45, "high": 10.55, "low": 10.45, "close": 10.50, "volume": 1000}
    r._manage(ts, bar, None)
    assert ts.half_filled is True
    assert ts.t1_shares_sold == 5
    assert ts.shares == 5


# ─── REP-5: trades_completed_today increment ─────────────────────────────────
def test_replay_t2_increments_trades_completed():
    r, ts = _make_replay_with_position(half_filled=True)
    r.submit_sell = MagicMock()
    bar = {"open": 10.99, "high": 11.05, "low": 10.95, "close": 11.0, "volume": 1000}
    r._manage(ts, bar, None)
    assert r.day.trades_completed_today == 1


def test_replay_stop_exit_increments_trades_completed():
    r, ts = _make_replay_with_position(half_filled=False)
    r.submit_sell = MagicMock()
    bar = {"open": 9.5, "high": 9.55, "low": 9.40, "close": 9.5, "volume": 1000}
    r._manage(ts, bar, None)
    assert r.day.trades_completed_today == 1


def test_replay_t1_only_does_not_increment_trades_completed():
    """T1-Partial ist noch kein abgeschlossener Trade — counter bleibt."""
    r, ts = _make_replay_with_position(half_filled=False, initial=10)
    r.submit_sell = MagicMock()
    bar = {"open": 10.45, "high": 10.55, "low": 10.45, "close": 10.50, "volume": 1000}
    r._manage(ts, bar, None)
    assert r.day.trades_completed_today == 0


# ─── Sanity: stop ohne T1 ────────────────────────────────────────────────────
def test_replay_stop_without_t1_no_t1_addition():
    r, ts = _make_replay_with_position(half_filled=False, initial=10)
    r.submit_sell = MagicMock()
    bar = {"open": 9.5, "high": 9.55, "low": 9.40, "close": 9.5, "volume": 1000}
    r._manage(ts, bar, None)
    expected = (9.5 - 10.0) * 10  # nur initial loss
    assert abs(r.day.realized_pnl - expected) < 0.01


# ─── Consecutive losses tracking parity ──────────────────────────────────────
def test_replay_stop_loss_increments_consecutive():
    r, ts = _make_replay_with_position(half_filled=False)
    r.submit_sell = MagicMock()
    bar = {"open": 9.5, "high": 9.55, "low": 9.40, "close": 9.5, "volume": 1000}
    r._manage(ts, bar, None)
    assert r.day.consecutive_losses == 1


def test_replay_t2_win_resets_consecutive():
    r, ts = _make_replay_with_position(half_filled=True)
    r.day.consecutive_losses = 1
    r.submit_sell = MagicMock()
    bar = {"open": 10.99, "high": 11.05, "low": 10.95, "close": 11.0, "volume": 1000}
    r._manage(ts, bar, None)
    assert r.day.consecutive_losses == 0
