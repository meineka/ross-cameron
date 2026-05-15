"""Test TIME_NEW_ENTRIES_START sweep.

Diagnose der 13 pilot trades zeigt:
- 9:30-10:15: 5 trades, 2 wins (40%), +$3.95
- 10:30+:     8 trades, 8 wins (100%), +$116.52

Hypothese: Skip Power-Hour Early (9:30-10:30) liefert besseres Sharpe.
"""
from __future__ import annotations
import sys, logging
from datetime import time as dtime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def run(name, start_time, dates):
    orig = bot_mod.TIME_NEW_ENTRIES_START
    bot_mod.TIME_NEW_ENTRIES_START = start_time
    total_pnl = 0; total_trades = 0; daily = []
    wins = losses = spirals = 0
    try:
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
    finally:
        bot_mod.TIME_NEW_ENTRIES_START = orig
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2),
            "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nTIME_NEW_ENTRIES_START Sweep ({len(dates)} days)\n")
configs = [
    ("CURRENT 9:35",   dtime(9, 35)),
    ("9:45 (skip 1st 15m)",  dtime(9, 45)),
    ("10:00 (skip 30m)",     dtime(10, 0)),
    ("10:15 (skip 45m)",     dtime(10, 15)),
    ("10:30 (skip PH)",      dtime(10, 30)),
    ("10:45 (just past PH)", dtime(10, 45)),
]
results = []
for name, t in configs:
    r = run(name, t, dates)
    results.append(r)
    print(f"{name:<24}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<24} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 72)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<23} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
