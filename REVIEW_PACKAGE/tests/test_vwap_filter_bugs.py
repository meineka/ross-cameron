"""Audit-Iter 21 (2026-05-12): vwap_filter.py edge-case robustness.

Bug VWAP-2 (MED): session_vwap crashte mit KeyError bei bar ohne 'volume'
  oder andere Keys. handle_bar fing's ab aber bar wurde silent gedroppt.

Bug VWAP-4 (MED): negative volume in bar (Daten-Anomalie aus feed) wurde
  ungeprüft in cum_v aufsummiert → konnte cum_v in negatives Drift
  bringen oder VWAP verfälschen.

Plus strict-mode für is_above_vwap (analog catalyst_filter Iter 10).
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug VWAP-2: defensive bar-validation ───────────────────────────────────
def test_session_vwap_skips_bar_with_missing_key():
    """Bar ohne 'volume' wird geskipt, nicht crash."""
    from vwap_filter import session_vwap
    bars = [
        {"high": 10, "low": 9, "close": 9.5},  # missing volume
        {"high": 10, "low": 9, "close": 9.5, "volume": 1000},
    ]
    v = session_vwap(bars)
    # Sollte den second valid bar nutzen, vwap = (10+9+9.5)/3 = 9.5
    assert v is not None
    assert abs(v - 9.5) < 0.01


def test_session_vwap_skips_bar_with_none_value():
    from vwap_filter import session_vwap
    bars = [
        {"high": 10, "low": 9, "close": None, "volume": 1000},
        {"high": 10, "low": 9, "close": 9.5, "volume": 500},
    ]
    v = session_vwap(bars)
    assert v is not None


def test_session_vwap_skips_nan_values():
    from vwap_filter import session_vwap
    import math
    bars = [
        {"high": float("nan"), "low": 9, "close": 9.5, "volume": 1000},
        {"high": 10, "low": 9, "close": 9.5, "volume": 500},
    ]
    v = session_vwap(bars)
    assert v is not None
    # Nur der valid bar zählt
    assert abs(v - 9.5) < 0.01


# ─── Bug VWAP-4: negative volume ────────────────────────────────────────────
def test_session_vwap_skips_negative_volume_bar():
    """Negative volume = Daten-Anomalie → bar skipped."""
    from vwap_filter import session_vwap
    bars = [
        {"high": 100, "low": 100, "close": 100, "volume": -1000},  # corrupt
        {"high": 10, "low": 9, "close": 9.5, "volume": 500},
    ]
    v = session_vwap(bars)
    # Sollte nur den valid bar nutzen, VWAP = 9.5
    assert v is not None
    assert abs(v - 9.5) < 0.01


def test_session_vwap_zero_volume_bars_dont_contribute():
    """Zero-volume bars haben 0 weight (Doji), aber kein crash."""
    from vwap_filter import session_vwap
    bars = [
        {"high": 100, "low": 100, "close": 100, "volume": 0},  # Doji
        {"high": 10, "low": 9, "close": 9.5, "volume": 500},
    ]
    v = session_vwap(bars)
    assert v is not None
    # Doji adds 0 to numerator + 0 to denominator → only second bar
    assert abs(v - 9.5) < 0.01


def test_session_vwap_returns_none_when_no_valid_bars():
    from vwap_filter import session_vwap
    bars = [
        {"high": 100, "low": 100, "close": 100, "volume": -1000},
        {"high": 100, "low": 100, "close": 100, "volume": 0},
    ]
    v = session_vwap(bars)
    assert v is None


def test_session_vwap_empty_returns_none():
    from vwap_filter import session_vwap
    assert session_vwap([]) is None


# ─── is_above_vwap behavior ─────────────────────────────────────────────────
def test_is_above_vwap_returns_true_when_close_above():
    from vwap_filter import is_above_vwap
    bars = [{"high": 10, "low": 9, "close": 9.5, "volume": 1000}]
    assert is_above_vwap(bars, 10.0) is True


def test_is_above_vwap_returns_false_when_close_below():
    from vwap_filter import is_above_vwap
    bars = [{"high": 10, "low": 9, "close": 9.5, "volume": 1000}]
    assert is_above_vwap(bars, 9.0) is False


def test_is_above_vwap_returns_true_on_no_data_default():
    """Default = permissive: ohne Daten kein Veto."""
    from vwap_filter import is_above_vwap
    assert is_above_vwap([], 10.0) is True


def test_is_above_vwap_strict_returns_false_on_no_data():
    """Strict-Mode: ohne Daten = veto (False)."""
    from vwap_filter import is_above_vwap
    assert is_above_vwap([], 10.0, strict=True) is False


def test_is_above_vwap_strict_passes_when_above():
    """Strict + above VWAP → True."""
    from vwap_filter import is_above_vwap
    bars = [{"high": 10, "low": 9, "close": 9.5, "volume": 1000}]
    assert is_above_vwap(bars, 10.0, strict=True) is True


# ─── Volume-Weighted-Math ────────────────────────────────────────────────────
def test_vwap_volume_weighted_correctly():
    """Math sanity: high-volume bar dominiert."""
    from vwap_filter import session_vwap
    bars = [
        {"high": 10, "low": 10, "close": 10, "volume": 100},   # tp=10
        {"high": 20, "low": 20, "close": 20, "volume": 9900},  # tp=20
    ]
    v = session_vwap(bars)
    # VWAP = (10*100 + 20*9900) / (100+9900) = 199000/10000 = 19.90
    assert abs(v - 19.90) < 0.01


def test_vwap_with_string_input_skips_gracefully():
    """Defensive: string in float-feld → skip statt crash."""
    from vwap_filter import session_vwap
    bars = [
        {"high": "garbage", "low": 9, "close": 9.5, "volume": 1000},
        {"high": 10, "low": 9, "close": 9.5, "volume": 500},
    ]
    v = session_vwap(bars)
    assert v is not None
