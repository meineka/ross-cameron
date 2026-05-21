"""1-Monat Backtest: Top-10 Hot-Stocks pro Tag, alle Cameron-Varianten.

User: "kannst du einen test jetzt machen der 1 monat lang die hottest
stocks täglich wählt und tradet ganz normal gemäss cameron samt
varianten"

Run modes:
  strict   = Cameron-original (pole≥4%, retrace≤50%, vol≥1.5x, no FBO)
  moderate = same with 1 FBO allowed (Phase-75 baseline)
  loose    = Phase-69 (pole≥2.5%, retrace≤70%, vol≥1.2x, 2 FBO allowed)

Spread model (Phase-80): 50bps bid/ask spread on every fill.
"""
import sys, io, os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Save the raw stdout file descriptor BEFORE v2 wraps it (v2's wrap then
# becomes detached when we reset, so we lose it cleanly)
_raw_stdout_fd = os.dup(1)

# Import v2 — it wraps sys.stdout to UTF-8 TextIOWrapper
import backtest_bull_flag_v2 as v2
from backtest_top10_per_day import pick_top10_per_day

# Reset sys.stdout to a fresh UTF-8 wrapper on the saved fd
sys.stdout = io.TextIOWrapper(os.fdopen(_raw_stdout_fd, "wb"),
                                encoding="utf-8", errors="replace",
                                line_buffering=True)

DATA_DIR = HERE / "data_pilot"
LAST_N_DAYS = 30   # 1 trading month
TOP_N = 10
MAX_LOSS_PER_TRADE = 50.0


def run_backtest(pattern_name: str, max_fbo: int, candidates: pd.DataFrame,
                 intraday: pd.DataFrame):
    """Returns dict with trade stats."""
    top = pick_top10_per_day(candidates, top_n=TOP_N)
    top["session_date"] = pd.to_datetime(top["date"]).dt.date

    all_trades = []
    for _, row in top.iterrows():
        ticker = row["ticker"]
        session_date = row["session_date"]
        sub = intraday[
            (intraday["ticker"] == ticker) &
            (intraday["session_date"] == session_date)
        ]
        if len(sub) < 30:
            continue
        day_bars = sub.set_index("time").sort_index()
        day_bars = day_bars[["open", "high", "low", "close", "volume", "ticker"]]
        day_bars = day_bars.dropna(subset=["open", "high", "low", "close"])
        day_bars = day_bars[[v2.is_rth(t) for t in day_bars.index]]
        if len(day_bars) < 30:
            continue
        detected = v2.detect_bull_flag(day_bars, max_fbo_score=max_fbo, debug=False)
        for tr in detected:
            tr = v2.simulate_exit(tr, day_bars)
            tr_dict = tr.__dict__.copy() if hasattr(tr, "__dict__") else dict(tr)
            tr_dict["rank_in_day"] = int(row["rank"])
            all_trades.append(tr_dict)

    if not all_trades:
        return {
            "pattern": pattern_name, "trades": 0,
            "wins": 0, "losses": 0, "win_rate": 0,
            "pnl": 0, "max_dd": 0, "pf": 0, "avg_trade": 0,
            "trades_per_day": 0,
        }

    df = pd.DataFrame(all_trades)
    df["risk_per_share"] = (df["entry_price"] - df["stop_price"]).clip(lower=0.05)
    df["shares"] = (MAX_LOSS_PER_TRADE / df["risk_per_share"]).astype(int)
    df["pnl_usd"] = df["pnl_per_share"] * df["shares"]

    pnl = df["pnl_usd"]
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    n = wins + losses
    sum_w = float(pnl[pnl > 0].sum())
    sum_l = -float(pnl[pnl < 0].sum())
    pf = sum_w / sum_l if sum_l > 0 else float("inf")
    cum = pnl.cumsum()
    dd = float((cum - cum.cummax()).min())
    unique_days = df["date"].nunique() if "date" in df.columns else len(set(d[:10] for d in df.get("entry_time", [])))

    return {
        "pattern": pattern_name,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": 100 * wins / n if n else 0,
        "pnl": float(pnl.sum()),
        "max_dd": dd,
        "pf": pf,
        "avg_trade": float(pnl.sum()) / n if n else 0,
        "trades_per_day": n / unique_days if unique_days else 0,
        "df": df,  # for per-rank breakdown
    }


def main():
    print("=" * 78)
    print("1-MONAT BACKTEST — Hot-Stocks Top-10/Tag — alle Cameron-Varianten")
    print("=" * 78)
    print()

    # Load data
    candidates = pd.read_parquet(DATA_DIR / "candidates.parquet")
    intraday = pd.read_parquet(DATA_DIR / "intraday_5m.parquet")

    # Normalize timestamps
    tc = next((cn for cn in intraday.columns
                 if "time" in cn.lower() or "date" in cn.lower()), None)
    if tc != "time":
        intraday = intraday.rename(columns={tc: "time"})
    intraday["time"] = pd.to_datetime(intraday["time"], utc=True, errors="coerce")
    intraday = intraday.dropna(subset=["time"])
    intraday["session_date"] = intraday["time"].dt.tz_convert("America/New_York").dt.date

    # Filter to last 30 trading days
    all_days = sorted(intraday["session_date"].unique())
    last_n = all_days[-LAST_N_DAYS:]
    intraday = intraday[intraday["session_date"].isin(last_n)]
    candidates["session_date"] = pd.to_datetime(candidates["date"]).dt.date
    candidates = candidates[candidates["session_date"].isin(last_n)]

    print(f"Window: {last_n[0]} → {last_n[-1]} ({len(last_n)} trading days)")
    print(f"Candidate-days: {len(candidates):,}")
    print(f"Intraday rows: {len(intraday):,}")
    print(f"Top-N: {TOP_N} per day, ${MAX_LOSS_PER_TRADE}/trade max loss")
    print(f"Spread model: {v2.SPREAD_BPS}bps (Phase-80)")
    print()

    # Run all 3 variants
    variants = [
        ("strict",   0),
        ("moderate", 1),
        ("loose",    2),
    ]

    results = []
    for name, max_fbo in variants:
        print(f"Running {name} (max_fbo={max_fbo})...")
        r = run_backtest(name, max_fbo, candidates, intraday)
        results.append(r)

    # Summary
    print()
    print("=" * 78)
    print(f"{'Pattern':<12}{'Trades':>8}{'Win%':>8}{'PnL$':>10}{'MaxDD$':>10}"
          f"{'PF':>8}{'Avg$':>10}{'T/day':>8}")
    print("-" * 78)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        print(f"{r['pattern']:<12}"
              f"{r['trades']:>8}"
              f"{r['win_rate']:>7.1f}%"
              f"{r['pnl']:>+10.0f}"
              f"{r['max_dd']:>10.0f}"
              f"{pf_str:>8}"
              f"{r['avg_trade']:>+10.2f}"
              f"{r['trades_per_day']:>8.1f}")
    print("=" * 78)
    print()

    # Per-rank breakdown for the BEST variant
    best = max(results, key=lambda r: r["pnl"])
    print(f"PER-RANK breakdown for BEST variant ({best['pattern']}):")
    if "df" in best and not best["df"].empty:
        for rank, grp in best["df"].groupby("rank_in_day"):
            r_pnl = grp["pnl_usd"].sum()
            r_n = len(grp)
            r_wins = (grp["pnl_usd"] > 0).sum()
            r_wr = 100 * r_wins / r_n if r_n else 0
            print(f"  rank #{rank:>2}: {r_n:>4} trades  win={r_wr:.0f}%  PnL ${r_pnl:>+7.0f}")
    print()

    # Recommendation
    print("RECOMMENDATION:")
    if best["pnl"] > 0:
        print(f"  -> Use STRATEGY_VARIANT={best['pattern']} for cloud trading.")
        print(f"     Expected ~{best['trades_per_day']:.1f} trades/day, "
              f"~${best['avg_trade']:.0f}/trade avg, "
              f"win-rate {best['win_rate']:.0f}%, PF {best['pf']:.2f}.")
    else:
        print(f"  -> All variants UNPROFITABLE on this 30-day window.")
        print(f"     Best: {best['pattern']} PnL ${best['pnl']:+.0f}.")
        print(f"     Consider: wider stops, different universe, or DON'T TRADE.")


if __name__ == "__main__":
    main()
