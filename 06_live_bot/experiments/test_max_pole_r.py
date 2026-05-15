"""Test Max-Pole-Size-Filter.

Diagnose: Trades mit t2R > 3 sind 2/3 LOSSES (FGI, MSC). EDSA (t2R=3.15)
ist Marginal-Win $6. Hypothese: cappen wir pole_height auf max 1.5R
(=> t2 max 3R von entry).
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def patched_detect(max_t2_r):
    """Wrapped detect_bull_flag — reject if t2R > max."""
    _orig = bot_mod.detect_bull_flag
    def custom(bars):
        ok, params = _orig(bars)
        if not ok:
            return ok, params
        ep = params["entry_price"]; sp = params["stop_price"]
        risk = ep - sp
        if risk <= 0:
            return ok, params
        t2_r = (params["target2"] - ep) / risk
        if t2_r > max_t2_r:
            return False, params
        return ok, params
    return custom


def run(name, max_t2_r, dates):
    if max_t2_r is None:
        # baseline
        total_pnl = 0; total_trades = 0; daily = []
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
    else:
        orig = bot_mod.detect_bull_flag
        bot_mod.detect_bull_flag = patched_detect(max_t2_r)
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
            bot_mod.detect_bull_flag = orig
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "max_t2_r": max_t2_r, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nMax-T2-R Sweep ({len(dates)} days)\n")
configs = [
    ("CURRENT (no cap)",  None),
    ("Cap t2R<=4.0",       4.0),
    ("Cap t2R<=3.5",       3.5),
    ("Cap t2R<=3.0",       3.0),
    ("Cap t2R<=2.5",       2.5),
    ("Cap t2R<=2.0",       2.0),
]
results = []
for name, cap in configs:
    r = run(name, cap, dates)
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
