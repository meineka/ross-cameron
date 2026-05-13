"""Audit-Iter 18 (2026-05-12): intraday_rescan + WS-resubscribe robustness.

Bug WS-2 (HIGH): ws_task in thread könnte hängen wenn ws.stop_ws() den
  SDK-internen run() nicht killt. Folge: ws_loop blockiert ewig im
  while-not-done-Check, kein Resubscribe, neue Symbole bleiben unsubscribed.
  Fix: asyncio.wait_for + cancel mit 10s/2s Timeouts.

Plus Tests für intraday_rescan-Dict-Mutation (added/removed/kept).
"""
from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _ts(symbol: str, rank: int, score: float):
    """Echte TickerState, kein lightweight mock — intraday_rescan iteriert
    self.tickers.values() für final log und greift in_position ab."""
    import bot as bot_mod
    return bot_mod.TickerState(symbol=symbol, rank=rank, score=score)


def _make_bot_with_tickers(symbols_ranks: dict[str, int], in_positions: set = None):
    """Bot mit gefüllten tickers + optional in_position-flags."""
    import bot as bot_mod
    in_positions = in_positions or set()
    b = bot_mod.Bot.__new__(bot_mod.Bot)
    b.executor = MagicMock()
    b.day = bot_mod.DayState()
    b.logger = MagicMock()
    b.tickers = {}
    b._pending_ws_resubscribe = False
    for sym, rank in symbols_ranks.items():
        ts = bot_mod.TickerState(symbol=sym, rank=rank, score=float(rank))
        ts.in_position = sym in in_positions
        b.tickers[sym] = ts
    return b


# ─── intraday_rescan logic ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rescan_keeps_in_position_dropouts():
    """Symbol dropt aus top-10 aber in_position → KEEP, no del."""
    b = _make_bot_with_tickers({"A": 1, "B": 2, "C": 3}, in_positions={"B"})
    new_cands = [
        _ts("A", 1, 1.0),
        _ts("D", 2, 2.0),
    ]
    with patch("bot.premarket_scan", return_value=new_cands):
        await b.intraday_rescan()
    # B war removed aber in_position → bleibt
    assert "B" in b.tickers
    # C war removed und NICHT in_position → weg
    assert "C" not in b.tickers
    # D ist neu hinzugefügt
    assert "D" in b.tickers
    # _pending_ws_resubscribe muss gesetzt sein (D wurde hinzugefügt)
    assert b._pending_ws_resubscribe is True


@pytest.mark.asyncio
async def test_rescan_empty_keeps_current_watchlist():
    """premarket_scan returns [] → keine Änderung."""
    b = _make_bot_with_tickers({"A": 1, "B": 2})
    with patch("bot.premarket_scan", return_value=[]):
        await b.intraday_rescan()
    assert set(b.tickers.keys()) == {"A", "B"}
    assert b._pending_ws_resubscribe is False


@pytest.mark.asyncio
async def test_rescan_updates_ranks_for_kept_symbols():
    """Symbol stays in top-10 but rank changed → update rank+score."""
    b = _make_bot_with_tickers({"A": 1, "B": 2})
    new_cands = [
        _ts("B", 1, 99.0),
        _ts("A", 2, 50.0),
    ]
    with patch("bot.premarket_scan", return_value=new_cands):
        await b.intraday_rescan()
    assert b.tickers["B"].rank == 1
    assert b.tickers["B"].score == 99.0
    assert b.tickers["A"].rank == 2


@pytest.mark.asyncio
async def test_rescan_exception_does_not_corrupt_state():
    """premarket_scan raises → tickers unchanged, kein resubscribe-flag."""
    b = _make_bot_with_tickers({"A": 1, "B": 2})
    with patch("bot.premarket_scan", side_effect=RuntimeError("scan crashed")):
        await b.intraday_rescan()
    assert set(b.tickers.keys()) == {"A", "B"}
    assert b._pending_ws_resubscribe is False


@pytest.mark.asyncio
async def test_rescan_no_resubscribe_when_no_diff():
    """Wenn neue watchlist == alte → kein resubscribe nötig."""
    b = _make_bot_with_tickers({"A": 1, "B": 2})
    new_cands = [
        _ts("A", 1, 1.0),
        _ts("B", 2, 2.0),
    ]
    with patch("bot.premarket_scan", return_value=new_cands):
        await b.intraday_rescan()
    # Same set → keine adds/removes → kein resubscribe
    assert b._pending_ws_resubscribe is False


@pytest.mark.asyncio
async def test_rescan_multiple_adds_and_removes():
    """Komplexes Szenario: 2 removed, 2 added, 1 kept."""
    b = _make_bot_with_tickers({"A": 1, "B": 2, "C": 3})
    new_cands = [
        _ts("A", 1, 1.0),
        _ts("D", 2, 2.0),
        _ts("E", 3, 3.0),
    ]
    with patch("bot.premarket_scan", return_value=new_cands):
        await b.intraday_rescan()
    assert "A" in b.tickers
    assert "D" in b.tickers
    assert "E" in b.tickers
    assert "B" not in b.tickers
    assert "C" not in b.tickers
    assert b._pending_ws_resubscribe is True


# ─── handle_bar race-condition ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_handle_bar_drops_unknown_symbol():
    """Bar für deleted symbol → silent drop, no crash."""
    b = _make_bot_with_tickers({"A": 1})
    bar = MagicMock()
    bar.symbol = "B"  # not in tickers
    bar.open = 10; bar.high = 10.1; bar.low = 9.9
    bar.close = 10.05; bar.volume = 1000
    bar.timestamp = MagicMock()
    bar.timestamp.astimezone.return_value.time.return_value = MagicMock()
    # Should not crash
    await b.handle_bar(bar)


@pytest.mark.asyncio
async def test_handle_bar_ignores_after_symbol_removed():
    """Sequenz: bar handled → rescan removes symbol → next bar gleiche
    Symbol → dropped."""
    b = _make_bot_with_tickers({"A": 1, "B": 2})
    bar_a = MagicMock()
    bar_a.symbol = "A"
    bar_a.open = bar_a.high = bar_a.low = bar_a.close = 10.0
    bar_a.volume = 1000
    bar_a.timestamp = MagicMock()
    bar_a.timestamp.astimezone.return_value.time.return_value = MagicMock()
    await b.handle_bar(bar_a)
    # Bar für A ok ─ bar_dict appended
    assert len(b.tickers["A"].bars) == 1
    # Rescan removed A
    del b.tickers["A"]
    # Bar für A nochmal → silent drop
    bar_a2 = MagicMock()
    bar_a2.symbol = "A"
    bar_a2.open = bar_a2.high = bar_a2.low = bar_a2.close = 11.0
    bar_a2.volume = 2000
    bar_a2.timestamp = MagicMock()
    await b.handle_bar(bar_a2)
    # No crash, A bleibt removed
    assert "A" not in b.tickers
