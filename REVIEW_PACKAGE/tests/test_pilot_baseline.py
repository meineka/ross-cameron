"""Pilot-Stats Regression: Backtest-Output darf nicht von Baseline abweichen.

Baseline-Werte aus PILOT_REPORT_V3.md (frozen).
"""
import pandas as pd
from pathlib import Path
import pytest

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
    df = pd.read_parquet(D / "trades_v2.parquet")
    # V2 loose baseline: 396 trades, 63.9% WR
    assert 380 <= len(df) <= 410, f"V2 loose trade count drift: {len(df)}"


@pytest.mark.skipif(not (D / "candidates.parquet").exists(), reason="bootstrap not done")
def test_candidates_universe_sanity():
    df = pd.read_parquet(D / "candidates.parquet")
    # All candidates must satisfy 5 Pillars
    assert df["close"].between(2, 20).all(), "candidate price out of range"
    assert (df["intraday_pct"] >= 10).all(), "candidate %-change below 10"
    assert (df["rvol_proxy"] >= 2).all(), "candidate rvol below 2"
