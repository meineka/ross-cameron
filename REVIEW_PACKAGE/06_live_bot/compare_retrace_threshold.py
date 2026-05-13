"""compare_retrace_threshold.py — Backtest FLAG_RETRACE_MAX_PCT 50 vs 60
across all available pilot days.

Reports per-day + total: trades, realized_pnl, win rate, spirals, peak drawdown.
"""
from __future__ import annotations
import sys
import logging
from pathlib import Path
from io import StringIO

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod


def run_one_day(target_date: str, retrace_pct: float) -> dict:
    """Returns dict with stats for that day at given threshold."""
    # Suppress log noise
    bot_mod.log.setLevel(logging.ERROR)

    # Patch threshold
    original = bot_mod.FLAG_RETRACE_MAX_PCT
    bot_mod.FLAG_RETRACE_MAX_PCT = retrace_pct
    try:
        rb = bot_mod.ReplayBot()
        rb.run(target_date)
        d = rb.day
        return {
            "date": target_date,
            "pnl": round(d.realized_pnl, 2),
            "peak": round(d.peak_pnl, 2),
            "trades": d.trades_completed_today,
            "consec_losses_final": d.consecutive_losses,
            "spiral": d.spiral_locked,
        }
    except Exception as e:
        return {"date": target_date, "error": str(e)[:80]}
    finally:
        bot_mod.FLAG_RETRACE_MAX_PCT = original


def main():
    # Get all available dates from pilot data
    bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
    bars = pd.read_parquet(bars_path)
    tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
    bars[tc] = pd.to_datetime(bars[tc], utc=True)
    dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())
    print(f"Backtesting {len(dates)} days: {dates[0]} - {dates[-1]}")
    print("=" * 80)
    print(f"{'Date':<12} {'BL trd':>6} {'BL pnl':>9} {'NEW trd':>7} {'NEW pnl':>9} {'Delta':>8}")
    print("-" * 80)

    totals = {"50_pnl": 0.0, "50_trd": 0, "50_win_days": 0, "50_loss_days": 0,
              "60_pnl": 0.0, "60_trd": 0, "60_win_days": 0, "60_loss_days": 0,
              "60_spiral_days": 0, "50_spiral_days": 0}

    threshold_b = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    threshold_a = 50.0
    print(f"Comparing {threshold_a}% vs {threshold_b}%\n")

    for d in dates:
        ds = d.isoformat()
        r50 = run_one_day(ds, threshold_a)
        r60 = run_one_day(ds, threshold_b)
        if "error" in r50 or "error" in r60:
            print(f"{ds}  ERR: {r50.get('error') or r60.get('error')}")
            continue
        delta = r60["pnl"] - r50["pnl"]
        marker = "+++" if delta > 5 else ("---" if delta < -5 else "")
        print(f"{ds}  {r50['trades']:>6}  ${r50['pnl']:>7.2f}  "
              f"{r60['trades']:>6}  ${r60['pnl']:>7.2f}  "
              f"{delta:>+7.2f} {marker}")

        totals["50_pnl"] += r50["pnl"]
        totals["50_trd"] += r50["trades"]
        totals["60_pnl"] += r60["pnl"]
        totals["60_trd"] += r60["trades"]
        if r50["pnl"] > 0: totals["50_win_days"] += 1
        elif r50["pnl"] < 0: totals["50_loss_days"] += 1
        if r60["pnl"] > 0: totals["60_win_days"] += 1
        elif r60["pnl"] < 0: totals["60_loss_days"] += 1
        if r50["spiral"]: totals["50_spiral_days"] += 1
        if r60["spiral"]: totals["60_spiral_days"] += 1

    print("=" * 80)
    print(f"\nSUMMARY ({len(dates)} days)")
    print(f"  BASELINE (retrace=50%):")
    print(f"    Total trades:       {totals['50_trd']}")
    print(f"    Total PnL:          ${totals['50_pnl']:+.2f}")
    print(f"    Win days:           {totals['50_win_days']}")
    print(f"    Loss days:          {totals['50_loss_days']}")
    print(f"    Spiral days:        {totals['50_spiral_days']}")
    print(f"  PROPOSED (retrace=60%):")
    print(f"    Total trades:       {totals['60_trd']}")
    print(f"    Total PnL:          ${totals['60_pnl']:+.2f}")
    print(f"    Win days:           {totals['60_win_days']}")
    print(f"    Loss days:          {totals['60_loss_days']}")
    print(f"    Spiral days:        {totals['60_spiral_days']}")
    delta_total = totals['60_pnl'] - totals['50_pnl']
    delta_trd = totals['60_trd'] - totals['50_trd']
    print(f"\n  DELTA:")
    print(f"    Extra trades:       {delta_trd:+d}")
    print(f"    Extra PnL:          ${delta_total:+.2f}")
    if totals['60_trd'] > totals['50_trd']:
        per_extra = delta_total / (totals['60_trd'] - totals['50_trd']) if delta_trd else 0
        print(f"    Avg PnL/extra trd:  ${per_extra:+.2f}")


if __name__ == "__main__":
    main()
