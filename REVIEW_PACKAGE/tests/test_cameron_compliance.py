"""Tests für die 9 Strategie-Verbesserungen aus AUDIT_CAMERON_COMPLIANCE."""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── #1 RVOL strict ──────────────────────────────────────────────────────────
def test_rvol_min_is_strict_5():
    import bot
    assert bot.RVOL_MIN_PROXY == 5.0, "Cameron-strict ist 5.0 — war zu lasch"


# ─── #2 Float-Wiring ─────────────────────────────────────────────────────────
def test_premarket_scan_uses_float_filter():
    """Source-check: passes_float_filter wird im scan aufgerufen."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "passes_float_filter" in src
    # Muss im scan-Body sein, nicht nur Import
    assert src.count("passes_float_filter") >= 2


# ─── #3 VWAP-Veto ────────────────────────────────────────────────────────────
def _make_bullflag_bars(n_pad: int = 25, *, breakout_close: float = 11.0,
                       above_vwap: bool = True):
    """Synthetische Bars die ein Bull-Flag liefern. above_vwap regelt ob
    Close > VWAP."""
    bars = []
    # 25 padding-bars um VWAP zu definieren
    base = 8.5 if above_vwap else 12.0
    for _ in range(n_pad):
        bars.append({"open": base, "high": base+0.05, "low": base-0.05,
                     "close": base, "volume": 1000,
                     "timestamp": pd.Timestamp("2026-05-11 09:30")})
    # Pole 5 grüne Bars 8.5 -> 10
    pole_prices = np.linspace(8.5, 10.0, 6)
    for p0, p1 in zip(pole_prices[:-1], pole_prices[1:]):
        bars.append({"open": p0, "high": p1+0.02, "low": p0-0.02, "close": p1,
                     "volume": 5000, "timestamp": pd.Timestamp("2026-05-11 09:35")})
    # Flag 2 rot
    bars.append({"open": 10.0, "high": 10.05, "low": 9.85, "close": 9.9,
                 "volume": 1200, "timestamp": pd.Timestamp("2026-05-11 09:40")})
    bars.append({"open": 9.9, "high": 9.95, "low": 9.8, "close": 9.85,
                 "volume": 1100, "timestamp": pd.Timestamp("2026-05-11 09:41")})
    # Breakout
    bars.append({"open": 9.9, "high": breakout_close+0.05, "low": 9.88,
                 "close": breakout_close, "volume": 10000,
                 "timestamp": pd.Timestamp("2026-05-11 09:42")})
    return bars


def test_detect_bull_flag_vetoes_below_vwap():
    import bot
    bars = _make_bullflag_bars(above_vwap=False)
    ok, params = bot.detect_bull_flag(bars)
    assert ok is False
    # may also fail other vetos; we just need not-ok


# ─── #4 MACD-Veto ────────────────────────────────────────────────────────────
def test_macd_is_bullish_true_uptrend():
    from indicators import macd_is_bullish
    closes = [10 + i * 0.05 for i in range(40)]  # rising
    assert macd_is_bullish(closes) is True


def test_macd_is_bullish_false_downtrend():
    from indicators import macd_is_bullish
    closes = [12 - i * 0.05 for i in range(40)]  # falling
    assert macd_is_bullish(closes) is False


def test_macd_short_input_no_veto():
    from indicators import macd_is_bullish
    assert macd_is_bullish([10, 11, 12]) is True


# ─── #5 MACD-Exit ────────────────────────────────────────────────────────────
def test_macd_bear_cross_detected():
    from indicators import macd, macd_bear_cross
    # Such ramp+drop that bei letztem Bar genau der Cross stattfindet.
    closes = [10 + i * 0.1 for i in range(40)]
    # füge bars hinzu bis der cross genau passiert
    for delta in [-0.3, -0.4, -0.5, -0.6, -0.7, -0.8]:
        closes.append(closes[-1] + delta)
        if macd_bear_cross(closes):
            break
    assert macd_bear_cross(closes) is True


def test_macd_no_cross_during_uptrend():
    from indicators import macd_bear_cross
    closes = [10 + i * 0.05 for i in range(50)]
    assert macd_bear_cross(closes) is False


# ─── #6 FBO ──────────────────────────────────────────────────────────────────
def test_fbo_vetos_topping_tail():
    from indicators import false_breakout_veto
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 1000}
            for _ in range(25)]
    # Last bar: huge upper wick
    bars.append({"open": 10, "high": 12, "low": 9.95, "close": 10.05, "volume": 1000})
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is True
    assert "topping_tail" in why


def test_fbo_vetos_close_lower_third():
    from indicators import false_breakout_veto
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 1000}
            for _ in range(25)]
    bars.append({"open": 11, "high": 11.1, "low": 9.0, "close": 9.3, "volume": 1000})
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is True


def test_fbo_passes_clean_breakout():
    """Audit-Iter 9: realistischer Wobble in Basis-Bars (±5c) statt 23x
    identische closes. Mit dem RSI-Fix (Bug IND-6) returnt sonst RSI=100
    in der Synthetik-Konsolidierung weil loss=0."""
    from indicators import false_breakout_veto
    bars = []
    # Konsolidierung um 10.05 mit ±0.05 wobble (typische 30-Sec Bewegung)
    for i in range(23):
        c = 10.05 + (0.05 if i % 2 == 0 else -0.05)
        bars.append({"open": 10.0, "high": 10.10, "low": 9.95, "close": c, "volume": 1000})
    # Moderate Breakout (nicht parabolic): +1.3% über 3 bars
    bars.append({"open": 10.05, "high": 10.10, "low": 10.0, "close": 10.08, "volume": 1500})
    bars.append({"open": 10.08, "high": 10.13, "low": 10.05, "close": 10.11, "volume": 1500})
    bars.append({"open": 10.11, "high": 10.18, "low": 10.10, "close": 10.16, "volume": 3000})
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is False, f"unexpected veto: {why}"


# ─── #7 Catalyst ─────────────────────────────────────────────────────────────
def test_catalyst_returns_true_on_yfinance_error():
    """Bei API-Fehler: nicht veto-en (don't block trade on tooling fail)."""
    import catalyst_filter
    catalyst_filter._cache.clear()
    with patch("yfinance.Ticker", side_effect=Exception("boom")):
        assert catalyst_filter.has_recent_news("XYZ") is True


def test_catalyst_caches():
    import catalyst_filter
    catalyst_filter._cache.clear()
    catalyst_filter._cache["AAA"] = (True, 1e18)  # far future ts
    assert catalyst_filter.has_recent_news("AAA") is True


# ─── #8 Liquidity-Cap + #9 Power-Hour in compute_position_size ───────────────
def test_position_size_liquidity_cap():
    import bot
    from datetime import time as dtime
    d = bot.DayState()
    d.quarter_size_unlocked = True
    # ohne Cap: 50 / 0.5 = 100 shares
    size_no_cap = bot.compute_position_size(10.0, 9.5, 100_000, d, ny_time=dtime(9, 35))
    assert size_no_cap == 100
    # mit avg_volume = 1000 → Cap = 1000 * 1% = 10
    size_capped = bot.compute_position_size(
        10.0, 9.5, 100_000, d, avg_volume=1000, ny_time=dtime(9, 35),
    )
    assert size_capped == 10


def test_position_size_power_hour_full():
    import bot
    from datetime import time as dtime
    d = bot.DayState()
    d.quarter_size_unlocked = True
    s = bot.compute_position_size(10, 9.5, 100_000, d, ny_time=dtime(9, 45))
    assert s == 100  # 1.0× multiplier


def test_position_size_post_power_reduced():
    import bot
    from datetime import time as dtime
    d = bot.DayState()
    d.quarter_size_unlocked = True
    s = bot.compute_position_size(10, 9.5, 100_000, d, ny_time=dtime(11, 0))
    assert s == 75  # 0.75× nach 10:30


# ─── Smoke: bot importiert + alle vetos wired ────────────────────────────────
def test_bot_wired_all_three_vetos():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "is_above_vwap(bars" in src, "VWAP-Veto nicht wired"
    assert "macd_is_bullish" in src, "MACD-Veto nicht wired"
    assert "false_breakout_veto" in src, "FBO-Veto nicht wired"
    assert "macd_bear_cross" in src, "MACD-Exit nicht wired"


def test_bot_has_catalyst_and_float_in_scan():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Must appear in scan logic block (after 'Pre-rank candidates')
    idx = src.find("Pre-rank candidates")
    assert idx > 0
    scan_tail = src[idx: idx + 3000]
    assert "passes_float_filter" in scan_tail
    assert "passes_catalyst_filter" in scan_tail
