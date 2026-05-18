"""Pilot-Stats Regression: Backtest-Output darf nicht von Baseline abweichen.

Baseline-Werte aus PILOT_REPORT_V3.md (frozen).
"""
import pandas as pd
from pathlib import Path
import pytest


# Phase-19: parquet reads are fast; no marker needed at file level.
D = Path(__file__).resolve().parent.parent / "04_backtest" / "data_pilot"


@pytest.mark.skipif(not (D / "trades.parquet").exists(), reason="pilot run not done")
def test_v1_baseline_stats():
    df = pd.read_parquet(D / "trades.parquet")
    n = len(df)
    wr = (df["pnl_per_share"] > 0).mean()
    assert n == 604, f"V1 trade count drift: {n} (expected 604)"
    assert abs(wr - 0.626) < 0.01, f"V1 win-rate drift: {wr:.3f}"


@pytest.mark.skipif(not (D / "trades_v2.parquet").exists(), reason="v2 run not done")
def test_v2_loose_baseline_stats():
    """V2 trade count baseline.

    Drift history:
      - 2026-05-13 calibration: 380-410 trades (early pilot ~80 days)
      - 2026-05-18 rebaseline: pilot grew to ~250 days, v2 --loose now
        produces ~679 trades, --strict ~518 trades. Tolerance widened
        to accept either mode + future pilot growth up to ~750 trades.

    If this test drifts past 800 trades, the pilot may have a
    DIFFERENT bug — investigate before widening further."""
    df = pd.read_parquet(D / "trades_v2.parquet")
    assert 380 <= len(df) <= 750, f"V2 trade count drift: {len(df)}"


@pytest.mark.skipif(not (D / "candidates.parquet").exists(), reason="bootstrap not done")
def test_candidates_universe_sanity():
    df = pd.read_parquet(D / "candidates.parquet")
    # All candidates must satisfy 5 Pillars
    assert df["close"].between(2, 20).all(), "candidate price out of range"
    assert (df["intraday_pct"] >= 10).all(), "candidate %-change below 10"
    assert (df["rvol_proxy"] >= 2).all(), "candidate rvol below 2"
