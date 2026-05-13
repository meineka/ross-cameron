"""Audit-Iteration 5 (2026-05-12): HARD_FLAT / market_close_all robustness.

HARD_FLAT ist der kritische Failsafe. Wenn er bricht, bleibt eine Position
übernacht offen — Day-Trading-Setup darf das nie passieren.

Vorher (Bug HF-1/HF-2/HF-9):
  - Single-shot close_all_positions ohne retry
  - Keine Verification dass positions wirklich 0
  - Bei Fehler: log und return — Position offen

Jetzt:
  - Retry mit max_attempts (3)
  - Polling bis positions==0 oder Timeout
  - Per-Position fallback market-sell
  - Returns bool (True = flat, False = CRITICAL)
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_executor():
    """AlpacaExecutor-Instanz mit gemocktem TradingClient."""
    import bot
    ex = bot.AlpacaExecutor.__new__(bot.AlpacaExecutor)
    ex.client = MagicMock()
    # cancel_open_orders_for iteriert get_orders → muss List sein
    ex.client.get_orders.return_value = []
    ex.dry_run = False
    return ex


def _pos(symbol: str, qty: int = 10):
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    return p


# ─── HAPPY PATH ──────────────────────────────────────────────────────────────
def test_returns_true_when_already_flat():
    """No open positions → close_all_positions wird gar nicht erst aufgerufen."""
    ex = _make_executor()
    ex.client.get_all_positions.return_value = []
    assert ex.market_close_all() is True
    ex.client.close_all_positions.assert_not_called()


def test_returns_true_when_closes_succeed_first_attempt():
    """Eine Position, close_all schließt sauber, poll zeigt 0 → True."""
    ex = _make_executor()
    ex.client.get_all_positions.side_effect = [
        [_pos("AAPL")],   # pre
        [],               # post-poll
    ]
    assert ex.market_close_all(verify_timeout_sec=2.0, poll_interval_sec=0.05) is True
    ex.client.close_all_positions.assert_called_with(cancel_orders=True)


# ─── Bug HF-1: RETRY ─────────────────────────────────────────────────────────
def test_retries_when_close_all_raises():
    """Wenn close_all_positions in attempt 1 exception wirft und Position
    bleibt → retry in attempt 2."""
    ex = _make_executor()
    ex.client.close_all_positions.side_effect = [
        RuntimeError("API timeout"),  # attempt 1
        None,                         # attempt 2 success
    ]
    # Stateful: erst nach dem zweiten close_all-Call wird positions leer
    calls = {"close": 0}
    orig_close = ex.client.close_all_positions

    def close_side(*a, **kw):
        calls["close"] += 1
        if calls["close"] == 1:
            raise RuntimeError("API timeout")

    def list_side(*a, **kw):
        return [] if calls["close"] >= 2 else [_pos("AAPL")]

    ex.client.close_all_positions.side_effect = close_side
    ex.client.get_all_positions.side_effect = list_side
    ok = ex.market_close_all(max_attempts=3, verify_timeout_sec=0.15,
                              poll_interval_sec=0.05)
    assert ok is True
    assert calls["close"] == 2


def test_max_attempts_respected_on_persistent_failure():
    """max_attempts=2 → close_all wird max 2x submitted."""
    ex = _make_executor()
    ex.client.close_all_positions.side_effect = RuntimeError("API down")
    ex.client.get_all_positions.return_value = [_pos("AAPL")]
    ex.client.submit_order.return_value = MagicMock(id="fb-1")
    ok = ex.market_close_all(max_attempts=2, verify_timeout_sec=0.1,
                              poll_interval_sec=0.05)
    # Position blieb wegen Mock — Fallback feuert Market-Sell
    assert ex.client.close_all_positions.call_count == 2
    # Fallback market-sell wurde versucht
    assert ex.client.submit_order.called


# ─── Bug HF-2: VERIFICATION ──────────────────────────────────────────────────
def test_polls_until_positions_empty():
    """close_all submitted, aber fills sind async — poll wartet bis 0."""
    ex = _make_executor()
    ex.client.get_all_positions.side_effect = [
        [_pos("AAPL"), _pos("MSFT")],  # pre
        [_pos("MSFT")],                # poll 1: AAPL filled
        [_pos("MSFT")],                # poll 2: still MSFT
        [],                            # poll 3: all filled
    ]
    ok = ex.market_close_all(verify_timeout_sec=2.0, poll_interval_sec=0.05)
    assert ok is True


def test_returns_false_when_positions_never_close():
    """API klappt, aber positions bleiben → final False + CRITICAL log."""
    ex = _make_executor()
    ex.client.get_all_positions.return_value = [_pos("STUCK")]
    ex.client.submit_order.return_value = MagicMock(id="fb-1")
    ok = ex.market_close_all(max_attempts=2, verify_timeout_sec=0.1,
                              poll_interval_sec=0.05)
    assert ok is False


# ─── Bug HF-9: PER-POSITION FALLBACK ─────────────────────────────────────────
def test_fallback_market_sell_per_position():
    """Wenn close_all_positions nicht greift, individual market-sell pro Symbol."""
    ex = _make_executor()
    # Stateful: positions bleiben bis fallback submit_order feuert
    state = {"submitted": 0}

    def list_side(*a, **kw):
        if state["submitted"] >= 2:
            return []
        return [_pos("AAPL", 5), _pos("MSFT", 3)]

    def submit_side(*a, **kw):
        state["submitted"] += 1
        m = MagicMock()
        m.id = f"fb-{state['submitted']}"
        return m

    ex.client.get_all_positions.side_effect = list_side
    ex.client.submit_order.side_effect = submit_side
    ok = ex.market_close_all(max_attempts=1, verify_timeout_sec=0.1,
                              poll_interval_sec=0.05)
    assert ok is True
    # 2 fallback market-sells (1 pro Symbol)
    assert state["submitted"] == 2
    syms_called = {c.args[0].symbol for c in ex.client.submit_order.call_args_list}
    assert syms_called == {"AAPL", "MSFT"}


def test_fallback_skips_zero_qty_positions():
    """Pos mit qty=0 darf nicht in market-sell münden."""
    ex = _make_executor()
    ex.client.get_all_positions.side_effect = [
        [_pos("AAPL", 0)],
        [_pos("AAPL", 0)],
        [_pos("AAPL", 0)],
        [_pos("AAPL", 0)],  # final still there but qty 0
    ]
    ex.client.submit_order.return_value = MagicMock(id="x")
    ex.market_close_all(max_attempts=1, verify_timeout_sec=0.1,
                         poll_interval_sec=0.05)
    # qty=0 ignored
    assert ex.client.submit_order.call_count == 0


# ─── DRY_RUN ─────────────────────────────────────────────────────────────────
def test_dry_run_returns_true_without_api_calls():
    ex = _make_executor()
    ex.dry_run = True
    assert ex.market_close_all() is True
    ex.client.close_all_positions.assert_not_called()
    ex.client.get_all_positions.assert_not_called()


# ─── ROBUSTHEIT GEGEN list-positions-FAILURE ─────────────────────────────────
def test_proceeds_when_pre_list_fails():
    """Wenn get_all_positions raised, bot weiß nicht ob flat → muss trotzdem
    close_all versuchen, statt blind 'True' zurückzugeben."""
    ex = _make_executor()
    ex.client.get_all_positions.side_effect = RuntimeError("API hiccup")
    ex.client.close_all_positions.return_value = None
    ok = ex.market_close_all(max_attempts=1, verify_timeout_sec=0.1,
                              poll_interval_sec=0.05)
    # close_all_positions wurde aufgerufen (Sicherheit zuerst)
    ex.client.close_all_positions.assert_called_once_with(cancel_orders=True)
    # Endresult: unknown == not flat → False
    assert ok is False
