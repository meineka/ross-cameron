"""Test Time-since-entry hard-exit.

Wenn Position N Bars (5-min) ohne T1 sitzt → market-close.
Cameron: "If it's not working in 5-10 minutes, get out."

Variants: N=3, 5, 6, 8, 10, 12 bars (= 15-60 minutes).
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def patched_manage_time_exit(max_bars_no_t1):
    QE_CENTS = bot_mod.QUICK_EXIT_THRESHOLD_CENTS
    QE_BARS  = bot_mod.QUICK_EXIT_BARS_LIMIT

    def custom_manage(self, ts, bar, ny_t):
        ts.bars_since_entry += 1
        # QE
        if (not ts.half_filled
                and ts.bars_since_entry <= QE_BARS
                and (ts.entry_price - bar["low"]) >= QE_CENTS):
            qe_px = ts.entry_price - QE_CENTS
            self.submit_sell(ts.symbol, ts.shares, qe_px, "QE")
            pnl = (qe_px - ts.entry_price) * ts.shares
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
        # Time-exit (only pre-T1)
        if not ts.half_filled and ts.bars_since_entry > max_bars_no_t1:
            exit_px = bar["close"]
            self.submit_sell(ts.symbol, ts.shares, exit_px, "TIME")
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
        # T1
        if not ts.half_filled and bar["high"] >= ts.target1_price:
            half = max(1, ts.shares // 2)
            self.submit_sell(ts.symbol, half, ts.target1_price, "T1")
            ts.half_filled = True
            ts.t1_shares_sold = half
            ts.shares -= half
            self.day.cents_per_share_cumulative += (ts.target1_price - ts.entry_price)
            return
        # T2
        if ts.half_filled and bar["high"] >= ts.target2_price:
            self.submit_sell(ts.symbol, ts.shares, ts.target2_price, "T2")
            r1 = (ts.target1_price - ts.entry_price) * ts.t1_shares_sold
            r2 = (ts.target2_price - ts.entry_price) * ts.shares
            pnl = r1 + r2
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.consecutive_losses = 0
            self.day.trades_completed_today += 1
            ts.in_position = False
            return
        # Stop
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            self.submit_sell(ts.symbol, ts.shares, stop, "stop")
            pnl = (stop - ts.entry_price) * ts.shares
            if ts.half_filled:
                pnl += (ts.target1_price - ts.entry_price) * ts.t1_shares_sold
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

    return custom_manage


def run(name, max_bars, dates):
    _orig = bot_mod.ReplayBot._manage
    bot_mod.ReplayBot._manage = patched_manage_time_exit(max_bars)
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
    return {"name": name, "max_bars": max_bars,
            "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2),
            "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nTime-Exit Sweep ({len(dates)} days, no T1 within N bars)\n")
configs = [
    ("CURRENT (no time-exit)", 9999),
    ("3 bars (15min)",  3),
    ("5 bars (25min)",  5),
    ("6 bars (30min)",  6),
    ("8 bars (40min)",  8),
    ("10 bars (50min)", 10),
    ("12 bars (60min)", 12),
]
results = []
for name, n in configs:
    r = run(name, n, dates)
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
