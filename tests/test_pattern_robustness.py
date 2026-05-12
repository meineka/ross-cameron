"""Pattern-Detector Robustheit gegen pathologische Eingaben."""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_detect_bull_flag_empty_bars():
    import bot
    ok, params = bot.detect_bull_flag([])
    assert ok is False


def test_detect_bull_flag_short_bars():
    """Minimum 10 bars für Pattern. Bei weniger: clean false."""
    import bot
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 1000}
            for _ in range(5)]
    ok, params = bot.detect_bull_flag(bars)
    assert ok is False


def test_detect_bull_flag_zero_volume():
    """Pattern mit Volume=0 sollte nicht crashen."""
    import bot
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10, "volume": 0}
            for _ in range(15)]
    ok, params = bot.detect_bull_flag(bars)
    # No assertion on truth, only that it doesn't raise
    assert ok in (True, False)


def test_detect_bull_flag_nan_close_doesnt_crash():
    """yfinance liefert manchmal NaN closes — soll graceful False geben."""
    import bot
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": float("nan"), "volume": 1000}
            for _ in range(15)]
    # If raises, that's the bug; assert it returns False cleanly
    try:
        ok, params = bot.detect_bull_flag(bars)
        assert ok is False
    except Exception as e:
        pytest.fail(f"detect_bull_flag crashed on NaN: {e}")


def test_detect_bull_flag_price_out_of_range():
    """Stock $50 (über $20) sollte nicht triggern."""
    import bot
    # Build artificial pole+flag at $50
    bars = []
    for i in range(20):
        bars.append({"open": 49+i*0.05, "high": 49.1+i*0.05, "low": 48.9+i*0.05,
                     "close": 49+i*0.05, "volume": 5000})
    ok, params = bot.detect_bull_flag(bars)
    assert ok is False


def test_detect_bull_flag_returns_valid_levels_if_signal():
    """Wenn Signal: entry > stop, target > entry (klassische R:R-Pflicht)."""
    import bot
    # Build a clean bull-flag in $5-$10 range
    bars = []
    # 20 padding bars at $5 to set up VWAP / SMA
    for _ in range(20):
        bars.append({"open": 5.0, "high": 5.05, "low": 4.95, "close": 5.0, "volume": 1000})
    # 5 green pole bars 5.0 -> 6.0
    for p in [5.2, 5.4, 5.6, 5.8, 6.0]:
        bars.append({"open": p-0.1, "high": p+0.05, "low": p-0.15, "close": p, "volume": 5000})
    # 2 red flag bars
    bars.append({"open": 6.0, "high": 6.02, "low": 5.85, "close": 5.90, "volume": 1500})
    bars.append({"open": 5.90, "high": 5.95, "low": 5.80, "close": 5.85, "volume": 1500})
    # green breakout candle above flag high (6.02) with high volume
    bars.append({"open": 5.90, "high": 6.15, "low": 5.88, "close": 6.10, "volume": 20000})
    ok, params = bot.detect_bull_flag(bars)
    if ok:
        # Critical R:R invariants
        assert params["entry_price"] > params["stop_price"]
        assert params["target1"] > params["entry_price"]
        assert params["target2"] >= params["target1"]
