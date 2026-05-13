"""Audit-Iter 31: two_source_scan wiring + edge tests.

Bug TS-1 (CRITICAL): two_source_scan war nirgendwo importiert → dead code.
  Bei yfinance-Outage hatte bot keinen fallback → silent leere watchlist.
  Fix: alert-logging wired in _premarket_scan_inner.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Module API edge cases ──────────────────────────────────────────────────
def test_yfinance_failure_ratio_basic():
    from two_source_scan import yfinance_failure_ratio
    assert yfinance_failure_ratio(100, 50) == 50.0
    assert yfinance_failure_ratio(100, 0) == 0.0
    assert yfinance_failure_ratio(100, 100) == 100.0


def test_yfinance_failure_ratio_zero_total_safe():
    """Division-by-zero guard."""
    from two_source_scan import yfinance_failure_ratio
    assert yfinance_failure_ratio(0, 0) == 0.0
    assert yfinance_failure_ratio(0, 100) == 0.0  # weird but safe


def test_should_fallback_at_exact_threshold():
    """Bei exakt 20% (threshold) → NOT fallback (strict >)."""
    from two_source_scan import should_fallback_to_alpaca
    assert should_fallback_to_alpaca(100, 20) is False
    assert should_fallback_to_alpaca(100, 21) is True


def test_should_fallback_below_threshold():
    from two_source_scan import should_fallback_to_alpaca
    assert should_fallback_to_alpaca(100, 15) is False
    assert should_fallback_to_alpaca(100, 19) is False


# ─── Alpaca snapshot fallback ────────────────────────────────────────────────
def test_alpaca_snapshot_handles_missing_bars():
    """Snapshot ohne daily_bar oder previous_daily_bar → skip silently."""
    from two_source_scan import alpaca_universe_snapshot
    from unittest.mock import MagicMock
    client = MagicMock()
    snap_ok = MagicMock()
    snap_ok.daily_bar = MagicMock(close=10.0)
    snap_ok.previous_daily_bar = MagicMock(close=9.0)
    snap_bad = MagicMock()
    snap_bad.daily_bar = None
    snap_bad.previous_daily_bar = None
    client.get_stock_snapshot.return_value = {"OK": snap_ok, "BAD": snap_bad}
    out = alpaca_universe_snapshot(client, ["OK", "BAD"])
    syms = [t[0] for t in out]
    assert "OK" in syms
    assert "BAD" not in syms


def test_alpaca_snapshot_handles_zero_prev_close():
    """prev_close=0 würde Division-by-zero → skip silently."""
    from two_source_scan import alpaca_universe_snapshot
    from unittest.mock import MagicMock
    client = MagicMock()
    snap = MagicMock()
    snap.daily_bar = MagicMock(close=10.0)
    snap.previous_daily_bar = MagicMock(close=0.0)
    client.get_stock_snapshot.return_value = {"X": snap}
    out = alpaca_universe_snapshot(client, ["X"])
    assert out == []  # filter weg


def test_alpaca_snapshot_handles_api_exception():
    """get_stock_snapshot raised → return [] statt crash."""
    from two_source_scan import alpaca_universe_snapshot
    from unittest.mock import MagicMock
    client = MagicMock()
    client.get_stock_snapshot.side_effect = RuntimeError("API down")
    out = alpaca_universe_snapshot(client, ["A", "B"])
    assert out == []


def test_alpaca_snapshot_computes_pct_correctly():
    from two_source_scan import alpaca_universe_snapshot
    from unittest.mock import MagicMock
    client = MagicMock()
    snap = MagicMock()
    snap.daily_bar = MagicMock(close=11.0)
    snap.previous_daily_bar = MagicMock(close=10.0)
    client.get_stock_snapshot.return_value = {"AAPL": snap}
    out = alpaca_universe_snapshot(client, ["AAPL"])
    assert len(out) == 1
    sym, price, pct = out[0]
    assert sym == "AAPL"
    assert price == 11.0
    assert abs(pct - 10.0) < 0.001  # 10% gain


# ─── Wiring in bot.py ────────────────────────────────────────────────────────
def test_bot_imports_two_source_scan():
    """REGRESSION TS-1: bot.py muss two_source_scan importieren UND nutzen."""
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert "from two_source_scan import" in src or "import two_source_scan" in src
    assert "should_fallback_to_alpaca" in src


def test_bot_logs_alert_when_yfinance_degraded():
    """Bot soll YFINANCE-DEGRADED loggen wenn fallback threshold überschritten."""
    import bot
    src = open(bot.__file__, encoding="utf-8").read()
    assert "YFINANCE-DEGRADED" in src
