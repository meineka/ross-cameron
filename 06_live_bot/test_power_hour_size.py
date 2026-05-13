"""Test if swapping POWER_HOUR_SIZE_MULT and POST_POWER_SIZE_MULT improves
risk-adjusted return.

Hypothesis: trade-time analysis shows Mid-Morning >> Power-Hour. Bot should
size DOWN in Power-Hour, UP in Mid-Morning. Currently has it BACKWARDS.
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def snapshot():
    return (bot_mod.POWER_HOUR_SIZE_MULT, bot_mod.POST_POWER_SIZE_MULT)


def restore(s):
    bot_mod.POWER_HOUR_SIZE_MULT, bot_mod.POST_POWER_SIZE_MULT = s


def run(name, ph_mult, post_mult, dates):
    bot_mod.POWER_HOUR_SIZE_MULT = ph_mult
    bot_mod.POST_POWER_SIZE_MULT = post_mult
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
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "ph_mult": ph_mult, "post_mult": post_mult,
            "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2),
            "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

snap = snapshot()
results = []
try:
    print(f"\nPOWER-HOUR vs POST-POWER size-mult sweep ({len(dates)} days)\n")
    configs = [
        # (name, PH-mult, POST-mult)
        ("CURRENT 1.0/0.75",  1.0,  0.75),  # bot today
        ("EQUAL 1.0/1.0",     1.0,  1.0),
        ("SWAP 0.75/1.0",     0.75, 1.0),
        ("AGGR SWAP 0.5/1.0", 0.5,  1.0),
        ("PH-OFF 0.25/1.0",   0.25, 1.0),  # almost skip PH
        ("BIAS 1.0/1.25",     1.0,  1.25),  # boost post
    ]
    for cfg in configs:
        r = run(*cfg, dates)
        results.append(r)
        print(f"{cfg[0]:<22} ph={cfg[1]:.2f} post={cfg[2]:.2f}: "
              f"trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
              f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} "
              f"sharpe={r['sharpe']}")
finally:
    restore(snap)

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sp':>3} {'Sharpe':>7}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['spirals']:>2}  {r['sharpe']:>6.2f}")
