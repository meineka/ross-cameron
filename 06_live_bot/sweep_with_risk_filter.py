"""Re-sweep config combinations with new MAX_RISK_PCT=8 as baseline.

Goal: find if any tune ON TOP of risk-filter further improves Cameron-style
risk-adjusted return.
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
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
    return (bot_mod.POLE_MIN_MOVE_PCT, bot_mod.POLE_TOPPING_TAIL_MAX,
            bot_mod.FLAG_RETRACE_MAX_PCT, bot_mod.BREAKOUT_VOL_FACTOR,
            bot_mod.MAX_RISK_PCT)


def restore(s):
    (bot_mod.POLE_MIN_MOVE_PCT, bot_mod.POLE_TOPPING_TAIL_MAX,
     bot_mod.FLAG_RETRACE_MAX_PCT, bot_mod.BREAKOUT_VOL_FACTOR,
     bot_mod.MAX_RISK_PCT) = s


def apply_config(pole_min, topping, retrace, vol, risk):
    bot_mod.POLE_MIN_MOVE_PCT = pole_min
    bot_mod.POLE_TOPPING_TAIL_MAX = topping
    bot_mod.FLAG_RETRACE_MAX_PCT = retrace
    bot_mod.BREAKOUT_VOL_FACTOR = vol
    bot_mod.MAX_RISK_PCT = risk


def run_config(name, pole_min, topping, retrace, vol, risk):
    apply_config(pole_min, topping, retrace, vol, risk)
    total_pnl = 0.0
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
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins / (wins+losses) * 100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl / abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {
        "name": name, "trades": total_trades,
        "pnl": round(total_pnl, 2), "win_rate": wr,
        "max_dd": round(mdd, 2), "spirals": spirals,
        "sharpe": sharpe,
    }


CONFIGS = [
    # name, pole_min, topping, retrace, vol, max_risk
    ("BASELINE",         5.0, 0.4, 50.0, 1.5, 8.0),   # current production
    ("topping-0.5",      5.0, 0.5, 50.0, 1.5, 8.0),   # add Option B
    ("topping-0.6",      5.0, 0.6, 50.0, 1.5, 8.0),
    ("pole-4-topping0.5",4.0, 0.5, 50.0, 1.5, 8.0),
    ("vol-2.0",          5.0, 0.4, 50.0, 2.0, 8.0),   # stricter volume
    ("retrace-60",       5.0, 0.4, 60.0, 1.5, 8.0),   # looser flag
    ("risk-6",           5.0, 0.4, 50.0, 1.5, 6.0),   # tighter risk
    ("risk-10",          5.0, 0.4, 50.0, 1.5, 10.0),  # looser risk
    ("aggressive",       4.0, 0.5, 60.0, 1.5, 8.0),
    ("conservative",     6.0, 0.4, 45.0, 2.0, 6.0),
]

snap = snapshot()
results = []
try:
    print(f"\n{'='*80}")
    print(f"RE-SWEEP with MAX_RISK_PCT=8 baseline ({len(CONFIGS)} configs, {len(dates)} days)")
    print(f"{'='*80}\n")
    for cfg in CONFIGS:
        print(f"Running {cfg[0]}...", end="", flush=True)
        r = run_config(*cfg)
        results.append(r)
        print(f" trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
              f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")
finally:
    restore(snap)

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sp':>3} {'Sharpe':>7}")
print("-" * 75)
for r in sorted(results, key=lambda x: -x['sharpe']):
    is_baseline = r['name'] == 'BASELINE'
    marker = '*' if is_baseline else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['spirals']:>2}  {r['sharpe']:>6.2f}")
print("-" * 75)
print("* = BASELINE (current production: MAX_RISK_PCT=8, others default)")
print("\nRanked by Sharpe-like (PnL/|MaxDD|) — risk-adjusted return")
