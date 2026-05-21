"""3-month backtest + ULTRA mode + per-stock breakdown.

User: "do 1 do 2 do 3" =
  1. ultra-mode backtest
  2. 3-month window
  3. per-stock profitability ranking
"""
import sys, io, os
import pandas as pd
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_fd = os.dup(1)
import backtest_bull_flag_v2 as v2
from backtest_top10_per_day import pick_top10_per_day
sys.stdout = io.TextIOWrapper(os.fdopen(_fd, "wb"), encoding="utf-8",
                                errors="replace", line_buffering=True)

DATA = HERE / "data_pilot"
TOP_N = 10
MAX_LOSS = 50.0


def run(name, max_fbo, c, i, ultra_overrides=False):
    """Run with optional ULTRA-style threshold overrides."""
    if ultra_overrides:
        # Phase-72 ultra-mode: even looser than loose
        orig_pole_min = v2.POLE_MOVE_MIN_PCT
        orig_pole_min_c = v2.POLE_CANDLES_MIN
        orig_pole_max_c = v2.POLE_CANDLES_MAX
        orig_topping = v2.POLE_TOPPING_TAIL_MAX_RATIO
        orig_retrace = v2.FLAG_RETRACE_MAX_PCT
        orig_vol = v2.BREAKOUT_VOL_FACTOR_MIN
        v2.POLE_MOVE_MIN_PCT = 1.0
        v2.POLE_CANDLES_MIN = 1
        v2.POLE_CANDLES_MAX = 15
        v2.POLE_TOPPING_TAIL_MAX_RATIO = 0.9
        v2.FLAG_RETRACE_MAX_PCT = 90.0
        v2.BREAKOUT_VOL_FACTOR_MIN = 1.0

    top = pick_top10_per_day(c, top_n=TOP_N)
    top["session_date"] = pd.to_datetime(top["date"]).dt.date

    all_trades = []
    for _, row in top.iterrows():
        sub = i[(i["ticker"] == row["ticker"]) & (i["session_date"] == row["session_date"])]
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
            td = tr.__dict__.copy() if hasattr(tr, "__dict__") else dict(tr)
            td["rank_in_day"] = int(row["rank"])
            all_trades.append(td)

    # Restore originals if ultra
    if ultra_overrides:
        v2.POLE_MOVE_MIN_PCT = orig_pole_min
        v2.POLE_CANDLES_MIN = orig_pole_min_c
        v2.POLE_CANDLES_MAX = orig_pole_max_c
        v2.POLE_TOPPING_TAIL_MAX_RATIO = orig_topping
        v2.FLAG_RETRACE_MAX_PCT = orig_retrace
        v2.BREAKOUT_VOL_FACTOR_MIN = orig_vol

    if not all_trades:
        return {"name": name, "trades": 0, "pnl": 0, "win_rate": 0,
                 "pf": 0, "df": pd.DataFrame()}
    df = pd.DataFrame(all_trades)
    df["risk_per_share"] = (df["entry_price"] - df["stop_price"]).clip(lower=0.05)
    df["shares"] = (MAX_LOSS / df["risk_per_share"]).astype(int)
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
    return {
        "name": name, "trades": n, "wins": wins, "losses": losses,
        "win_rate": 100 * wins / n if n else 0,
        "pnl": float(pnl.sum()), "max_dd": dd, "pf": pf,
        "avg": float(pnl.sum()) / n if n else 0,
        "df": df,
    }


def main():
    cand = pd.read_parquet(DATA / "candidates.parquet")
    intra = pd.read_parquet(DATA / "intraday_5m.parquet")
    tc = next((cn for cn in intra.columns if "time" in cn.lower() or "date" in cn.lower()), None)
    if tc != "time":
        intra = intra.rename(columns={tc: "time"})
    intra["time"] = pd.to_datetime(intra["time"], utc=True, errors="coerce")
    intra = intra.dropna(subset=["time"])
    intra["session_date"] = intra["time"].dt.tz_convert("America/New_York").dt.date
    cand["session_date"] = pd.to_datetime(cand["date"]).dt.date

    all_days = sorted(intra["session_date"].unique())

    # ─── 3-MONTH WINDOW ────────────────────────────────────────────
    print("=" * 78)
    print("BACKTEST: 3-MONTH WINDOW + ULTRA + PER-STOCK BREAKDOWN")
    print("=" * 78)
    print()

    last90 = all_days[-90:]
    c90 = cand[cand["session_date"].isin(last90)]
    i90 = intra[intra["session_date"].isin(last90)]
    print(f"Window: {last90[0]} -> {last90[-1]} ({len(last90)} trading days)")
    print(f"Top-{TOP_N}/day, ${MAX_LOSS}/trade max loss, spread={v2.SPREAD_BPS}bps")
    print()

    variants = [
        ("strict",   0, False),
        ("moderate", 1, False),
        ("loose",    2, False),
        ("ultra",    2, True),   # ULTRA threshold overrides
    ]

    results = []
    for name, fbo, ultra in variants:
        print(f"Running {name}...")
        r = run(name, fbo, c90, i90, ultra_overrides=ultra)
        results.append(r)

    print()
    print(f"{'Variant':<10}{'Trades':>8}{'Win%':>8}{'PnL$':>10}{'MaxDD$':>10}"
          f"{'PF':>8}{'Avg$':>9}{'T/day':>8}")
    print("-" * 78)
    for r in results:
        pf_s = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        t_day = r['trades'] / len(last90)
        print(f"{r['name']:<10}{r['trades']:>8}{r['win_rate']:>7.1f}%"
              f"{r['pnl']:>+10.0f}{r['max_dd']:>10.0f}{pf_s:>8}"
              f"{r['avg']:>+9.2f}{t_day:>8.2f}")
    print()

    # ─── PER-STOCK BREAKDOWN (best variant) ─────────────────────────
    best = max(results, key=lambda r: r["pnl"])
    df = best["df"]
    print(f"PER-STOCK profit ranking (variant={best['name']}):")
    print()
    if df.empty:
        print("  No trades")
    else:
        per = df.groupby("ticker").agg(
            n=("pnl_usd", "count"),
            wins=("pnl_usd", lambda x: (x > 0).sum()),
            pnl=("pnl_usd", "sum"),
            avg=("pnl_usd", "mean"),
        ).sort_values("pnl", ascending=False)
        per["win_pct"] = (100 * per["wins"] / per["n"]).round(0)

        print("TOP-15 winners:")
        print(f"  {'Ticker':<8}{'Trades':>8}{'Win%':>7}{'PnL$':>10}{'Avg$':>9}")
        for tkr, row in per.head(15).iterrows():
            print(f"  {tkr:<8}{int(row['n']):>8}{int(row['win_pct']):>6}%"
                  f"{row['pnl']:>+10.0f}{row['avg']:>+9.2f}")
        print()
        print("TOP-15 losers:")
        for tkr, row in per.tail(15).iloc[::-1].iterrows():
            print(f"  {tkr:<8}{int(row['n']):>8}{int(row['win_pct']):>6}%"
                  f"{row['pnl']:>+10.0f}{row['avg']:>+9.2f}")


if __name__ == "__main__":
    main()
