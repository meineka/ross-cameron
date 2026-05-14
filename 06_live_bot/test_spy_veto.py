"""Test SPY-Trend-Veto in ReplayBot.

Live-Bot fetches SPY-today-pct and skips trading on bear days.
ReplayBot bisher: SPY=0 → multiplier=1.0 → kein Veto.

Plan:
1. Fetch SPY daily bars für pilot date range
2. Pro pilot day: compute spy_pct vs previous close
3. Apply Bot's compute_spy_size_multiplier
4. Re-run pilot mit SPY-Veto active

Vergleich mit baseline.
"""
from __future__ import annotations
import os
import sys
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def fetch_spy_history(start_date, end_date):
    """SPY daily-bars via Alpaca."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    api_key = os.environ["APCA_API_KEY_ID"]
    api_secret = os.environ["APCA_API_SECRET_KEY"]
    client = StockHistoricalDataClient(api_key, api_secret)
    req = StockBarsRequest(
        symbol_or_symbols=["SPY"],
        timeframe=TimeFrame.Day,
        start=datetime.combine(start_date - timedelta(days=10), datetime.min.time()),
        end=datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
        feed="iex",
    )
    resp = client.get_stock_bars(req)
    return resp.df.reset_index()


def build_spy_pct_map(spy_df):
    """For each date, compute pct change vs prev close."""
    spy_df = spy_df.sort_values("timestamp")
    spy_df["date"] = pd.to_datetime(spy_df["timestamp"]).dt.date
    spy_df["prev_close"] = spy_df["close"].shift(1)
    spy_df["pct"] = (spy_df["close"] - spy_df["prev_close"]) / spy_df["prev_close"] * 100
    return dict(zip(spy_df["date"], spy_df["pct"]))


def run(name, spy_veto_pct, spy_pct_map, dates):
    orig_veto = bot_mod.SPY_TREND_VETO_PCT
    bot_mod.SPY_TREND_VETO_PCT = spy_veto_pct
    total_pnl = 0; total_trades = 0; daily = []
    wins = losses = spirals = 0
    skipped = 0
    try:
        for d in dates:
            spy_pct = spy_pct_map.get(d, 0.0)
            rb = bot_mod.ReplayBot()
            # Inject SPY into DayState
            rb.day.spy_pct_today = spy_pct
            rb.day.spy_size_multiplier = bot_mod.compute_spy_size_multiplier(spy_pct)
            if rb.day.spy_size_multiplier <= 0.0:
                skipped += 1
                daily.append(0.0)
                continue
            try:
                rb.run(d.isoformat())
            except Exception:
                continue
            pnl = round(rb.day.realized_pnl, 2)
            total_pnl += pnl
            total_trades += rb.day.trades_completed_today
            daily.append(pnl)
            if rb.day.spiral_locked: spirals += 1
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
    finally:
        bot_mod.SPY_TREND_VETO_PCT = orig_veto
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "veto": spy_veto_pct, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals,
            "skipped_days": skipped, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"Fetching SPY for {dates[0]} to {dates[-1]}…")
spy_df = fetch_spy_history(dates[0], dates[-1])
spy_pct_map = build_spy_pct_map(spy_df)
print(f"SPY-data: {len(spy_pct_map)} days")
# Show some samples
sample_dates = [d for d in dates if d in spy_pct_map][:5]
for d in sample_dates:
    print(f"  {d}: SPY {spy_pct_map[d]:+.2f}%")

print(f"\nSPY-Veto Sweep ({len(dates)} pilot days)\n")
configs = [
    ("Baseline (no SPY-veto)", -99.0),    # never trigger
    ("Strict -0.3% (current REDUCE)", -0.3),  # SPY_TREND_REDUCE_SIZE_PCT
    ("CURRENT -1.0%",          -1.0),
    ("Looser -1.5%",           -1.5),
    ("Very loose -2.0%",       -2.0),
]
results = []
for name, v in configs:
    r = run(name, v, spy_pct_map, dates)
    results.append(r)
    print(f"{name:<32} (skip={r['skipped_days']}): trd={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<32} {'Skp':>3} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 82)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<31} {r['skipped_days']:>3} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
