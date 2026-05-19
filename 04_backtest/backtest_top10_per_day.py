"""backtest_top10_per_day.py — Phase-75 (2026-05-19)

User observation: "der Scan soll genau so sein heute scanne ich alle
aktien und nehme 10 beste und trade da"

Problem in existing v2 backtest:
  tickers = intraday["ticker"].unique().tolist()  # ALL 1449 tickers!

This iterates over EVERY ticker × day combination, finding trades
even on symbols the live bot would never have watchlisted. The live
bot picks TOP-10 per day via TradingView premarket-scan, then only
runs bull-flag on those 10.

This script fixes that. Per trading day:
  1. Read candidates.parquet (already filtered to gap≥10%/price/RVOL)
  2. Rank by (intraday_pct × rvol_proxy) DESC — same as live bot's
     `score = (premarket_change * rvol)` in _premarket_scan_inner
  3. Take TOP-10
  4. Run bull-flag detector ONLY on those 10 (using 5m intraday data)
  5. Aggregate trades with proper position-sizing

This makes the backtest UNIVERSE the same as live (10 stocks per day,
not 1449). PnL numbers will be MUCH lower but trustworthy.

Usage:
    python backtest_top10_per_day.py
    python backtest_top10_per_day.py --top-n 10 --pattern strict
    python backtest_top10_per_day.py --pattern loose --max-loss 100
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).resolve().parent / "data_pilot"

# Reuse the existing detection logic from v2 backtest.
# Phase-75.1: imports made LAZY inside main() because v2 wraps stdout
# at module top-level, which breaks pytest's I/O capture. By deferring
# until main() actually runs, pick_top10_per_day() stays testable in
# isolation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_v2 = None


def _lazy_v2():
    global _v2
    if _v2 is None:
        import backtest_bull_flag_v2 as _m
        _v2 = _m
    return _v2


def pick_top10_per_day(candidates: pd.DataFrame,
                        top_n: int = 10) -> pd.DataFrame:
    """For each trading day, rank candidates by (intraday_pct ×
    rvol_proxy) DESC and take top_n. Same ranking the live bot uses
    via TradingView's premarket_change * rvol score.

    Returns DataFrame with columns: [date, ticker, score, rank]."""
    c = candidates.copy()
    c["score"] = c["intraday_pct"] * c["rvol_proxy"]
    c = c.sort_values(["date", "score"], ascending=[True, False])
    c["rank"] = c.groupby("date").cumcount() + 1
    return c[c["rank"] <= top_n][["date", "ticker", "score", "rank"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=10,
                     help="how many symbols per day (matches live bot's "
                          "TOP_N = 10)")
    ap.add_argument("--pattern", choices=["strict", "moderate", "loose"],
                     default="moderate")
    ap.add_argument("--max-loss-usd", type=float, default=50.0,
                     help="$50 strict / $100 relaxed/loose envelope")
    ap.add_argument("--out", type=str,
                     default="trades_top10_per_day.parquet")
    args = ap.parse_args()

    max_fbo = {"strict": 0, "moderate": 1, "loose": 2}[args.pattern]

    # Phase-75.1: trigger v2-import only here (NOT at module-top)
    v2 = _lazy_v2()
    detect_bull_flag = v2.detect_bull_flag
    simulate_exit = v2.simulate_exit
    is_rth = v2.is_rth

    # Load data
    candidates = pd.read_parquet(DATA_DIR / "candidates.parquet")
    intraday = pd.read_parquet(DATA_DIR / "intraday_5m.parquet")
    log.info("Loaded %d candidate-days, %d intraday rows",
              len(candidates), len(intraday))

    # Pick top-N per day (same ranking as live bot)
    top = pick_top10_per_day(candidates, top_n=args.top_n)
    log.info("Top-%d selection: %d (date, ticker) pairs across %d days",
              args.top_n, len(top), top["date"].nunique())

    # Normalize intraday timestamps
    tc = next((cn for cn in intraday.columns
                 if "time" in cn.lower() or "date" in cn.lower()), None)
    if tc != "time":
        intraday = intraday.rename(columns={tc: "time"})
    intraday["time"] = pd.to_datetime(intraday["time"], utc=True,
                                         errors="coerce")
    intraday = intraday.dropna(subset=["time"])
    intraday["session_date"] = (
        intraday["time"].dt.tz_convert("America/New_York").dt.date
    )

    # Normalize top.date to match intraday session_date type
    top["session_date"] = pd.to_datetime(top["date"]).dt.date

    # Run bull-flag detector ONLY on (ticker, day) pairs from top-N
    all_trades = []
    for _, row in top.iterrows():
        ticker = row["ticker"]
        session_date = row["session_date"]
        rank = row["rank"]
        sub = intraday[
            (intraday["ticker"] == ticker) &
            (intraday["session_date"] == session_date)
        ]
        if len(sub) < 30:
            continue  # not enough bars for pattern
        day_bars = sub.set_index("time").sort_index()
        day_bars = day_bars[["open", "high", "low", "close", "volume", "ticker"]]
        day_bars = day_bars.dropna(subset=["open", "high", "low", "close"])
        # RTH only
        day_bars = day_bars[[is_rth(t) for t in day_bars.index]]
        if len(day_bars) < 30:
            continue
        detected = detect_bull_flag(day_bars, max_fbo_score=max_fbo,
                                      debug=False)
        for tr in detected:
            tr = simulate_exit(tr, day_bars)
            # Track which rank this came from
            tr_dict = tr.__dict__.copy() if hasattr(tr, '__dict__') else dict(tr)
            tr_dict["rank_in_day"] = int(rank)
            tr_dict["day_score"] = float(row["score"])
            all_trades.append(tr_dict)

    log.info("Detected %d trades from top-%d universe",
              len(all_trades), args.top_n)

    if not all_trades:
        log.error("No trades found — empty result")
        return 1

    df = pd.DataFrame(all_trades)
    out_path = DATA_DIR / args.out
    df.to_parquet(out_path, index=False)
    log.info("Wrote %s", out_path)

    # Summary with proper sizing
    df["risk_per_share"] = (
        df["entry_price"] - df["stop_price"]
    ).clip(lower=0.05)
    df["shares"] = (args.max_loss_usd / df["risk_per_share"]).astype(int)
    df["pnl_usd"] = df["pnl_per_share"] * df["shares"]

    pnl = df["pnl_usd"]
    wins = (pnl > 0).sum()
    losses = (pnl < 0).sum()
    n = wins + losses
    sum_w = pnl[pnl > 0].sum()
    sum_l = -pnl[pnl < 0].sum()
    pf = sum_w / sum_l if sum_l > 0 else float("inf")
    cum = pnl.cumsum()
    dd = float((cum - cum.cummax()).min())
    ppd = pnl.sum() / abs(dd) if dd != 0 else 0

    print("=" * 72)
    print(f" TOP-{args.top_n}-PER-DAY BACKTEST ({args.pattern}, ${args.max_loss_usd}/trade)")
    print("=" * 72)
    print(f"  Universe:       {args.top_n} top per day x {top['date'].nunique()} days = {len(top)} candidate-days")
    print(f"  Trades found:   {n}")
    print(f"  Win-rate:       {100*wins/n:.1f}%")
    print(f"  Total PnL:      ${pnl.sum():+,.0f}")
    print(f"  Max-DD:         ${dd:,.0f}")
    print(f"  Profit-Factor:  {pf:.2f}")
    print(f"  Profit / |DD|:  {ppd:.1f}")
    print(f"  Avg per trade:  ${pnl.sum()/n if n else 0:+.2f}")

    # Per-rank breakdown
    print("\n  PnL per rank (where did winners come from?):")
    if "rank_in_day" in df.columns:
        for rank, grp in df.groupby("rank_in_day"):
            r_pnl = (grp["pnl_per_share"] * grp["shares"]).sum()
            r_n = len(grp)
            print(f"    rank #{rank:>2}: {r_n:>4} trades, PnL ${r_pnl:+,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
