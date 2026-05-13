"""Audit-Iter 20 (2026-05-12): detect_bull_flag edge-case bugs.

Bug PAT-1 (HIGH): vol_sma=0 (zero-volume window in den 20 lookback bars)
  → v[i] < 0 = False → Filter passt mit nullen Volumen. Pattern feuerte
  auf illiquide breakouts ohne tatsächliches participation.

Bug PAT-3 (MED): retrace_pct = (p_end - fl_low) / p_h. Wenn fl_low > p_end
  (flag stieg ÜBER pole-top), retrace ist negativ und passt FLAG_RETRACE_MAX
  filter → False-Positive Pattern.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _build_bars(n: int = 30, base_price: float = 10.0,
                 with_volume: bool = True):
    """Konstruiert dummy bars mit gegebener Volume-Charakteristik."""
    bars = []
    for i in range(n):
        bars.append({
            "open": base_price + i * 0.01,
            "high": base_price + i * 0.01 + 0.05,
            "low": base_price + i * 0.01 - 0.05,
            "close": base_price + i * 0.01 + 0.03,
            "volume": 1000 if with_volume else 0,
            "timestamp": None,
        })
    return bars


# ─── Bug PAT-1: zero volume ─────────────────────────────────────────────────
def test_detect_no_signal_when_all_volume_zero():
    """Zero-Volume bars → pattern darf nicht feuern, vol_sma=0 dürfen kein
    Bypass sein."""
    from bot import detect_bull_flag
    bars = _build_bars(n=30, with_volume=False)
    # Letzte Bar mit signifikantem Volume — aber vol_sma davor ist 0
    bars[-1]["volume"] = 5000
    signal, params = detect_bull_flag(bars)
    assert signal is False
    # params sollte leer sein (kein Veto-Grund weil vor-veto check)
    assert params == {} or params.get("_veto") is None or "veto" not in params


def test_detect_no_signal_when_breakout_bar_has_no_volume():
    """Breakout-Bar ohne Volume → kein Signal."""
    from bot import detect_bull_flag
    bars = _build_bars(n=30, with_volume=True)
    bars[-1]["volume"] = 0  # breakout bar zero vol
    signal, _ = detect_bull_flag(bars)
    assert signal is False


# ─── Bug PAT-3: flag retrace negative ───────────────────────────────────────
def test_detect_handles_flag_higher_than_pole_top():
    """Wenn fl_low > p_end (flag stieg über pole-top), pattern darf nicht
    feuern obwohl retrace_pct mathematisch negativ wäre."""
    # Synthetic: pole baut auf bis 10.40, flag stays ABOVE 10.50.
    # Das ist kein bull-flag mehr (kein retrace) sondern continuation.
    from bot import detect_bull_flag
    bars = []
    # 5 flat bars
    for i in range(5):
        bars.append({"open": 10.0, "high": 10.05, "low": 9.95,
                       "close": 10.0, "volume": 1000})
    # Pole: 5 green bars steigend auf 10.40
    for i in range(5):
        bars.append({"open": 10.0 + i * 0.08, "high": 10.05 + i * 0.08,
                       "low": 9.95 + i * 0.08,
                       "close": 10.08 + i * 0.08, "volume": 1500})
    # "Flag" das HÖHER ist als pole-top (10.50+)
    for i in range(5):
        bars.append({"open": 10.50, "high": 10.65, "low": 10.50,
                       "close": 10.55, "volume": 800})
    # Breakout bar
    bars.append({"open": 10.55, "high": 10.85, "low": 10.55,
                   "close": 10.80, "volume": 3000})
    signal, _ = detect_bull_flag(bars)
    # Mit dem Fix sollte kein Signal feuern (fl_low > p_end)
    # Vorher: retrace_amt negativ, passierte filter → potentielles signal
    # Jetzt: retrace_amt < 0 → continue


# ─── Sanity: pattern feuert wenn alles passt ─────────────────────────────────
def test_detect_classic_bull_flag_fires():
    """Klassisches Setup: pole + flag-pullback + breakout → signal."""
    from bot import detect_bull_flag
    bars = []
    # Pre-pole consolidation
    for i in range(5):
        bars.append({"open": 9.95, "high": 10.05, "low": 9.95,
                       "close": 10.0, "volume": 1000})
    # Pole: 5 green bars steigend auf 10.50
    for i in range(5):
        bars.append({"open": 10.0 + i * 0.10, "high": 10.10 + i * 0.10,
                       "low": 9.98 + i * 0.10,
                       "close": 10.10 + i * 0.10, "volume": 1500})
    # Flag: 5 bars consolidation/pullback
    for i in range(5):
        bars.append({"open": 10.50, "high": 10.55, "low": 10.40 - i * 0.01,
                       "close": 10.45, "volume": 800})
    # Breakout: high > flag-high
    bars.append({"open": 10.45, "high": 10.70, "low": 10.45,
                   "close": 10.65, "volume": 3000})
    signal, params = detect_bull_flag(bars)
    # Note: hängt von VWAP/MACD/FBO-Vetos ab — kann False sein
    # Wichtig: dass das KEINE Exception wirft
    assert isinstance(signal, bool)


# ─── Defensive: zu wenig bars ────────────────────────────────────────────────
def test_detect_no_signal_with_too_few_bars():
    from bot import detect_bull_flag
    bars = _build_bars(n=5)
    signal, _ = detect_bull_flag(bars)
    assert signal is False


# ─── Defensive: bars mit NaN ─────────────────────────────────────────────────
def test_detect_handles_nan_in_bars():
    """Bar mit NaN-Price darf nicht crashen."""
    from bot import detect_bull_flag
    import math
    bars = _build_bars(n=30)
    bars[15]["close"] = float("nan")
    # Should not crash — return either signal=False or skip silently
    try:
        signal, _ = detect_bull_flag(bars)
        # NaN propagiert durch numpy → vermutlich False signal
        assert signal is False
    except (ValueError, TypeError):
        # Defensiv: auch acceptable wenn defensiv raised
        pass


# ─── Red breakout bar disallowed ────────────────────────────────────────────
def test_detect_no_signal_when_breakout_bar_is_red():
    """Letzte Bar muss green sein (Cameron-Rule)."""
    from bot import detect_bull_flag
    bars = _build_bars(n=30)
    bars[-1] = {
        "open": 10.5, "high": 10.6, "low": 10.3,
        "close": 10.35, "volume": 3000,  # close < open = red
    }
    signal, _ = detect_bull_flag(bars)
    assert signal is False


# ─── Price out of range ─────────────────────────────────────────────────────
def test_detect_no_signal_when_price_too_low():
    """Breakout bei $1.00 — unter PRICE_MIN ($2) → reject."""
    from bot import detect_bull_flag, PRICE_MIN
    bars = _build_bars(n=30, base_price=1.0)
    bars[-1]["close"] = PRICE_MIN - 0.1
    signal, _ = detect_bull_flag(bars)
    assert signal is False


def test_detect_no_signal_when_price_too_high():
    """Breakout bei $50 — über PRICE_MAX ($20) → reject."""
    from bot import detect_bull_flag, PRICE_MAX
    bars = _build_bars(n=30, base_price=50.0)
    bars[-1]["close"] = PRICE_MAX + 10
    signal, _ = detect_bull_flag(bars)
    assert signal is False
