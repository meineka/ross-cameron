"""Test T2 = R-multiple statt pole_height.

Current: T2 = entry + 2 * pole_height (variable depending on pole)
Cameron-literal: T2 = entry + 2 * (entry - stop) = 2R consistent
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def patched_detect(t2_multiplier_of_r, use_pole=False):
    """Returns a wrapped detect_bull_flag that overrides T2 calculation."""
    _orig = bot_mod.detect_bull_flag

    def custom(bars):
        ok, params = _orig(bars)
        if not ok:
            return ok, params
        if not use_pole:
            ep = params["entry_price"]
            sp = params["stop_price"]
            risk = ep - sp
            new_t2 = round(ep + t2_multiplier_of_r * risk, 2)
            # Optional: keep psych-level upgrade
            next_half = (int(ep * 2) + 1) / 2.0
            params["target2"] = max(new_t2, next_half) if next_half > ep + 0.05 else new_t2
        # if use_pole: leave T2 as-is (default = pole_height based)
        return ok, params
    return custom


def run(name, t2_mult, dates, use_pole=False):
    _orig = bot_mod.detect_bull_flag
    bot_mod.detect_bull_flag = patched_detect(t2_mult, use_pole=use_pole)
    total_pnl = 0
    total_trades = 0
    daily = []
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
        bot_mod.detect_bull_flag = _orig
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2), "spirals": spirals,
            "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nT2 Definition Sweep ({len(dates)} days)\n")
configs = [
    ("CURRENT pole-based",   None, True),
    ("T2 = 1.5R",            1.5,  False),
    ("T2 = 2.0R",            2.0,  False),
    ("T2 = 2.5R",            2.5,  False),
    ("T2 = 3.0R",            3.0,  False),
    ("T2 = 4.0R",            4.0,  False),
]
results = []
for cfg in configs:
    name, mult, use_pole = cfg
    r = run(name, mult or 0, dates, use_pole=use_pole)
    results.append(r)
    print(f"{name:<20} trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} "
          f"sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
