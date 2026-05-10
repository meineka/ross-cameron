"""Regression-Test: Replay 2026-04-15 must produce known-good stats.

Wenn Backtest-Logik versehentlich verändert wird, schlägt dieser Test fehl.
"""
import json, sys, io, subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
PILOT_DATA = ROOT / "04_backtest" / "data_pilot" / "intraday_5m.parquet"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing — run bootstrap.py")
def test_replay_2026_04_15_baseline():
    """Replay-Output muss Baseline-Stats reproduzieren."""
    out = subprocess.run(
        ["python", "bot.py", "--replay", "2026-04-15"],
        cwd=ROOT / "06_live_bot",
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, f"replay exit-code {out.returncode}: {out.stderr}"
    log = out.stdout + out.stderr
    # Expected from baseline run (after price-min bug fix):
    #   3 trades, BIRD + MNTS winners, $12.15 PnL
    assert "Top-10 for 2026-04-15" in log
    assert "BIRD" in log
    assert "MNTS" in log
    assert "Daily realized PnL: $12.15" in log, f"PnL drift! Output:\n{log[-2000:]}"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing")
def test_replay_filters_low_price_stocks():
    """HUBC ($0.17) darf NICHT mehr getradet werden (Regression-Test für Bug-Fix)."""
    out = subprocess.run(
        ["python", "bot.py", "--replay", "2026-04-15"],
        cwd=ROOT / "06_live_bot",
        capture_output=True, text=True, timeout=60,
    )
    log = out.stdout + out.stderr
    # HUBC is in top-10 but should NOT trade because intraday price drops below $2
    assert "BUY HUBC" not in log, "HUBC unter $2 darf nicht getradet werden"


@pytest.mark.skipif(not PILOT_DATA.exists(), reason="pilot data missing")
def test_scan_only_produces_top10():
    """Scanner muss 10 Tickers produzieren."""
    out = subprocess.run(
        ["python", "bot.py", "--scan-only"],
        cwd=ROOT / "06_live_bot",
        capture_output=True, text=True, timeout=600,
    )
    assert out.returncode == 0
    log = out.stdout
    assert "TOP-10 WATCHLIST" in log
    rank_count = sum(1 for line in log.split("\n") if "rank" in line and "score" in line)
    assert rank_count == 10, f"expected 10 ranks, got {rank_count}"
