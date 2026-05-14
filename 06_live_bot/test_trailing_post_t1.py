"""Test Trailing-Stop nach T1.

Track höchsten high seit T1-Hit. Stop = max(BE, highest_post_t1 - trail_frac * risk).

Variants: trail-frac 0.25, 0.50, 0.75, 1.0, 1.5 (R-units).
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def patched_manage_trailing(trail_r):
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
        # T1
        if not ts.half_filled and bar["high"] >= ts.target1_price:
            half = max(1, ts.shares // 2)
            self.submit_sell(ts.symbol, half, ts.target1_price, "T1")
            ts.half_filled = True
            ts.t1_shares_sold = half
            ts.shares -= half
            ts._trail_high = bar["high"]  # init trailing high
            self.day.cents_per_share_cumulative += (ts.target1_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= bot_mod.QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
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
        # Stop logic
        if ts.half_filled:
            # update trailing high
            if not hasattr(ts, "_trail_high") or bar["high"] > ts._trail_high:
                ts._trail_high = bar["high"]
            risk = ts.target1_price - ts.entry_price  # 1R
            trail_stop = ts._trail_high - trail_r * risk
            stop = max(ts.entry_price, trail_stop)
        else:
            stop = ts.stop_price
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


def run(name, trail_r, dates):
    _orig = bot_mod.ReplayBot._manage
    bot_mod.ReplayBot._manage = patched_manage_trailing(trail_r)
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
    return {"name": name, "trail_r": trail_r,
            "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2),
            "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nTrailing-Stop-Post-T1 Sweep ({len(dates)} days)\n")
configs = [
    ("CURRENT BE only",  999.0),  # = effectively BE (trail-stop way below)
    ("Trail 0.5R",        0.5),
    ("Trail 0.75R",       0.75),
    ("Trail 1.0R",        1.0),
    ("Trail 1.5R",        1.5),
    ("Trail 2.0R",        2.0),
]
results = []
for name, tr in configs:
    r = run(name, tr, dates)
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
