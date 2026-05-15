"""FLAG_RETRACE_MAX_PCT sweep.

Cameron-Praxis: "Flag should retrace 38-50% of pole, no more."
Bot aktuell: 50% (Cameron-max). Could 38% (Fibonacci) tighter sein?
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def run(name, val, dates):
    orig = bot_mod.FLAG_RETRACE_MAX_PCT
    bot_mod.FLAG_RETRACE_MAX_PCT = val
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
        bot_mod.FLAG_RETRACE_MAX_PCT = orig
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "val": val, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nFLAG_RETRACE_MAX_PCT Sweep ({len(dates)} days)\n")
configs = [
    ("Tight 25%",     25.0),
    ("Fib 38%",       38.0),
    ("Mid 45%",       45.0),
    ("CURRENT 50%",   50.0),
    ("Loose 60%",     60.0),
    ("Very loose 70", 70.0),
]
results = []
for name, v in configs:
    r = run(name, v, dates)
    results.append(r)
    print(f"{name:<22}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 72)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
