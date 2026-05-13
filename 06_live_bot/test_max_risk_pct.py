"""Backtest MAX_RISK_PCT filter — reject trades where (entry-stop)/entry > X%.

Compare baseline vs 8%, 10%, 12% thresholds.
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)

# Need to patch ReplayBot to add the filter
_orig_run = bot_mod.ReplayBot.run

# Use module-level for closure
_max_risk_pct = [None]

def _patched_run(self, target_date: str):
    # Monkey-patch detect_bull_flag to filter on max-risk-pct
    _orig_detect = bot_mod.detect_bull_flag
    def filtered_detect(bars):
        ok, params = _orig_detect(bars)
        if ok and _max_risk_pct[0] is not None:
            ep = params["entry_price"]
            sp = params["stop_price"]
            if ep > 0:
                risk_pct = (ep - sp) / ep * 100
                if risk_pct > _max_risk_pct[0]:
                    return False, {"_veto": f"risk_pct_{risk_pct:.1f}_over_{_max_risk_pct[0]}"}
        return ok, params
    bot_mod.detect_bull_flag = filtered_detect
    try:
        return _orig_run(self, target_date)
    finally:
        bot_mod.detect_bull_flag = _orig_detect


def run_with_threshold(threshold_or_none, dates):
    _max_risk_pct[0] = threshold_or_none
    total_pnl = 0
    total_trades = 0
    wins = losses = 0
    daily_pnl = []
    spirals = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
        rb.run = lambda td, _self=rb: _patched_run(_self, td)
        try:
            rb.run(d.isoformat())
        except Exception:
            continue
        total_pnl += rb.day.realized_pnl
        total_trades += rb.day.trades_completed_today
        daily_pnl.append(rb.day.realized_pnl)
        if rb.day.spiral_locked: spirals += 1
        if rb.day.realized_pnl > 0: wins += 1
        elif rb.day.realized_pnl < 0: losses += 1
    # Drawdown
    cum = peak = mdd = 0
    for p in daily_pnl:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {
        "threshold": threshold_or_none,
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "win_days": wins,
        "loss_days": losses,
        "spiral_days": spirals,
        "max_dd": round(mdd, 2),
        "win_rate": round(wins / (wins+losses) * 100, 0) if (wins+losses) else 0,
    }


bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nBacktest MAX_RISK_PCT filter over {len(dates)} days\n")
print(f"{'Threshold':<12} {'Trades':>7} {'Total PnL':>11} {'Win days':>9} {'Loss days':>10} {'Spiral':>7} {'MaxDD':>9} {'Win%':>6}")
print("-" * 80)
for t in [None, 12.0, 10.0, 8.0, 6.0]:
    r = run_with_threshold(t, dates)
    name = "BASELINE" if t is None else f"<= {t:.0f}%"
    print(f"{name:<12} {r['total_trades']:>7} ${r['total_pnl']:>+8.2f}  {r['win_days']:>8} {r['loss_days']:>9} {r['spiral_days']:>6} ${r['max_dd']:>+7.2f}  {r['win_rate']:.0f}%")
