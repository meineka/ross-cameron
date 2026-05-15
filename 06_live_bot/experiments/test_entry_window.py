"""Backtest entry-time windows: when should new entries be allowed?"""
from __future__ import annotations
import sys, logging
from pathlib import Path
from datetime import time as dtime
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)

from bot import find_pilot_data_paths
bars_path, _ = find_pilot_data_paths()
if bars_path is None:
    raise FileNotFoundError("pilot data not found at backtest_data/ or 04_backtest/data_pilot/")
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())


def snapshot():
    return (bot_mod.TIME_NEW_ENTRIES_START, bot_mod.TIME_NEW_ENTRIES_END)

def restore(s):
    bot_mod.TIME_NEW_ENTRIES_START, bot_mod.TIME_NEW_ENTRIES_END = s

def run(name, start, end):
    bot_mod.TIME_NEW_ENTRIES_START = start
    bot_mod.TIME_NEW_ENTRIES_END = end
    total_pnl = 0
    total_trades = 0
    daily = []
    wins = losses = spirals = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
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
    cum=peak=mdd=0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2), "spirals": spirals,
            "sharpe": sharpe}


snap = snapshot()
results = []
try:
    print(f"Entry-Time Window Backtest ({len(dates)} pilot days)")
    print(f"{'='*70}\n")
    configs = [
        ("BASELINE 9:35-11:30",   dtime(9, 35), dtime(11, 30)),
        ("PowerHour-only 9:35-10:30", dtime(9, 35), dtime(10, 30)),
        ("Skip-PH 10:00-11:30",   dtime(10, 0),  dtime(11, 30)),
        ("Skip-PH 10:30-11:30",   dtime(10, 30), dtime(11, 30)),
        ("Skip-PH 10:30-12:00",   dtime(10, 30), dtime(12, 0)),
        ("Late 11:00-12:00",      dtime(11, 0),  dtime(12, 0)),
    ]
    for cfg in configs:
        print(f"Running {cfg[0]}...", end="", flush=True)
        r = run(*cfg)
        results.append(r)
        print(f" trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
              f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")
finally:
    restore(snap)

print(f"\n{'Window':<28} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sp':>3} {'Sharpe':>7}")
print("-" * 75)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'BASELINE' in r['name'] else ' '
    print(f"{marker}{r['name']:<27} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['spirals']:>2}  {r['sharpe']:>6.2f}")
