"""Audit-Iter 9 (2026-05-12): indicators.py edge-case robustness.

Gefundene Bugs:
  IND-3: false_breakout_veto crashed (KeyError) bei malformed bar
  IND-6: rsi() returnte 50 in monotone uptrend statt 100
         → false_breakout_veto RSI>80-Regel feuerte nicht bei
           parabolischem Chase = genau der Setup den sie filtern soll
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug IND-6: RSI in monotone uptrend ──────────────────────────────────────
def test_rsi_returns_100_in_monotone_uptrend():
    """Reines Steigen ohne Down-Bar → RSI muss 100 sein, NICHT 50."""
    from indicators import rsi
    closes = [10 + i * 0.1 for i in range(30)]
    r = rsi(closes, 14)
    assert r == 100.0, f"expected 100 (extreme up), got {r}"


def test_rsi_returns_0_in_monotone_downtrend():
    """Reines Fallen → RSI muss 0 sein (extreme down)."""
    from indicators import rsi
    closes = [10 - i * 0.1 for i in range(30)]
    r = rsi(closes, 14)
    assert r == 0.0, f"expected 0 (extreme down), got {r}"


def test_rsi_returns_50_in_flat():
    """Konstanter Preis: gain=0, loss=0 → 50 (neutral, kein Trend)."""
    from indicators import rsi
    closes = [10.0] * 30
    r = rsi(closes, 14)
    assert r == 50.0


def test_rsi_returns_50_with_insufficient_data():
    """< period+1 Bars → fallback 50."""
    from indicators import rsi
    closes = [10.0, 10.1, 10.2]
    r = rsi(closes, 14)
    assert r == 50.0


def test_rsi_handles_partial_correction_correctly():
    """Mix aus 9 grünen + 1 roten Bar — RSI sollte hoch sein (~85-95)."""
    from indicators import rsi
    closes = [10.0]
    for i in range(20):
        closes.append(closes[-1] + 0.5)
    closes.append(closes[-1] - 0.1)  # 1 kleines pullback
    r = rsi(closes, 14)
    assert r > 80, f"expected RSI > 80 in strong uptrend, got {r}"


# ─── Bug IND-3: false_breakout_veto KeyError ─────────────────────────────────
def test_fbo_does_not_crash_on_missing_high():
    """Malformed bar (fehlender 'high'-Key) muss kein crash auslösen."""
    from indicators import false_breakout_veto
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 1}
            for _ in range(25)]
    del bars[-1]["high"]
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is False
    assert why == ""


def test_fbo_does_not_crash_on_none_value():
    """Bar mit None statt float — defensive accept."""
    from indicators import false_breakout_veto
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 1}
            for _ in range(25)]
    bars[-1]["low"] = None
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is False


def test_fbo_does_not_crash_on_string_value():
    """Bar mit String — defensive accept."""
    from indicators import false_breakout_veto
    bars = [{"open": 10, "high": 10.1, "low": 9.9, "close": 10.05, "volume": 1}
            for _ in range(25)]
    bars[-1]["close"] = "garbage"
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is False


# ─── Bug IND-6 wirkt sich auf FBO aus ─────────────────────────────────────────
def test_fbo_vetos_parabolic_uptrend_via_rsi():
    """REGRESSION: Vorher returnte rsi 50 bei monotonem Up → FBO ließ
    parabolische Chases durch. Jetzt feuert RSI-Regel."""
    from indicators import false_breakout_veto
    bars = []
    for i in range(25):
        p = 10 + i * 0.3
        bars.append({"open": p, "high": p + 0.05, "low": p - 0.02,
                     "close": p + 0.04, "volume": 1000})
    vetoed, why = false_breakout_veto(bars)
    assert vetoed is True
    assert "rsi_overbought" in why


# ─── MACD-Edge-Cases ─────────────────────────────────────────────────────────
def test_macd_bullish_returns_false_with_nan_in_closes():
    """NaN in closes propagiert durch EWM → final NaN → False (kein bullish)."""
    from indicators import macd_is_bullish
    closes = [10.0] * 30
    closes[15] = float("nan")
    assert macd_is_bullish(closes) is False


def test_macd_bullish_returns_true_with_too_few_bars():
    """< 30 bars → permissive default True (Cameron: 'kein Veto')."""
    from indicators import macd_is_bullish
    assert macd_is_bullish([10.0, 10.1, 10.2]) is True


def test_macd_bear_cross_returns_false_with_too_few_bars():
    """< 30 bars → no cross signal."""
    from indicators import macd_bear_cross
    assert macd_bear_cross([10.0] * 10) is False


def test_macd_bear_cross_eventually_fires_in_downtrend():
    """Steigender Trend, dann Crash — irgendwo in der Reversal muss das
    Bear-Cross-Signal mindestens einmal True sein."""
    from indicators import macd_bear_cross
    closes = [10.0]
    for _ in range(50):
        closes.append(closes[-1] * 1.005)
    # Simuliere bar-by-bar das Crash-Szenario und check ob bear-cross
    # mindestens 1x triggert
    found = False
    for _ in range(20):
        closes.append(closes[-1] * 0.97)
        if macd_bear_cross(closes):
            found = True
            break
    assert found, "macd_bear_cross fired never during sharp downtrend"
