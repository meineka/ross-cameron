"""Test Quick-Exit-Implementierung in ReplayBot.

Live-Bot hat schon QUICK_EXIT_THRESHOLD_CENTS=0.30 + QUICK_EXIT_BARS_LIMIT=5.
ReplayBot kennt diese Logik nicht.

Variants:
- baseline (no QE)
- 30c absolute QE (5-bar window)
- 0.5R relative QE (5-bar window)
- 0.75R relative QE (5-bar window)
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def patched_manage_with_qe(quick_threshold_func, bars_limit=5):
    """Erweitert ReplayBot._manage um Quick-Exit-Logik."""
    _orig_manage = bot_mod.ReplayBot._manage

    def custom_manage(self, ts, bar, ny_t):
        # Quick-Exit: wenn vor T1 und innerhalb der ersten N Bars Preis
        # mindestens 'threshold' unter entry → exit zum bar-close.
        if not ts.half_filled:
            # bars_since_entry zählen
            if not hasattr(ts, "_bars_since_entry_replay"):
                ts._bars_since_entry_replay = 0
            ts._bars_since_entry_replay += 1
            if ts._bars_since_entry_replay <= bars_limit:
                threshold = quick_threshold_func(ts)
                # Exit if low has dropped >= threshold below entry
                if (ts.entry_price - bar["low"]) >= threshold:
                    # Use bar["close"] or entry-threshold price for QE
                    exit_px = ts.entry_price - threshold
                    self.submit_sell(ts.symbol, ts.shares, exit_px, "QE")
                    pnl = (exit_px - ts.entry_price) * ts.shares
                    self.day.realized_pnl += pnl
                    self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
                    self.day.trades_completed_today += 1
                    if pnl <= 0:
                        self.day.consecutive_losses += 1
                        if self.day.consecutive_losses >= 2:
                            self.day.spiral_locked = True
                    else:
                        self.day.consecutive_losses = 0
                    ts.in_position = False
                    return
        return _orig_manage(self, ts, bar, ny_t)
    return custom_manage


def run(name, threshold_func_or_none, dates):
    _orig = bot_mod.ReplayBot._manage
    if threshold_func_or_none is not None:
        bot_mod.ReplayBot._manage = patched_manage_with_qe(threshold_func_or_none)
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
        bot_mod.ReplayBot._manage = _orig
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

print(f"\nQuick-Exit Sweep ({len(dates)} days)\n")
configs = [
    ("BASELINE (no QE)", None),
    ("30c absolute",     lambda ts: 0.30),
    ("0.50R relative",   lambda ts: 0.50 * (ts.entry_price - ts.stop_price)),
    ("0.75R relative",   lambda ts: 0.75 * (ts.entry_price - ts.stop_price)),
    ("20c absolute",     lambda ts: 0.20),
    ("0.40R relative",   lambda ts: 0.40 * (ts.entry_price - ts.stop_price)),
]
results = []
for name, f in configs:
    r = run(name, f, dates)
    results.append(r)
    print(f"{name:<22}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 72)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'BASELINE' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
