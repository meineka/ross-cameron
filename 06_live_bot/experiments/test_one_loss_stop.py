"""Test variants of loss-tolerance:
- Current: 2 consecutive losses → spiral_locked
- Variant: 1 loss → stop (Cameron "first loss done for day")
- Variant: 1 loss but only above $X loss size
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def snapshot():
    return (
        bot_mod.SPIRAL_DETECTION_CONSECUTIVE_LOSSES if hasattr(bot_mod, "SPIRAL_DETECTION_CONSECUTIVE_LOSSES") else 2,
    )


# Override the spiral-check in ReplayBot._manage on the fly
# Approach: monkey-patch self.day.spiral_locked logic
# Simpler: just patch ReplayBot._manage with a wrapper that triggers
# spiral after configurable threshold

def run_with_threshold(name, max_consec_losses, dates, halt_on_any_loss_above=None):
    """Run replay with custom spiral threshold."""
    total_pnl = 0
    total_trades = 0
    daily = []
    wins = losses = spirals = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
        # Wrap _manage to enforce custom threshold
        _orig_manage = rb._manage
        def custom_manage(ts, bar, ny_t, _self=rb, _orig=_orig_manage):
            # Only allow trades if not "stopped"
            if _self.day.spiral_locked:
                return  # already stopped
            _orig(ts, bar, ny_t)
            # Custom: stop after N consecutive losses OR after one big loss
            if _self.day.consecutive_losses >= max_consec_losses:
                _self.day.spiral_locked = True
            if (halt_on_any_loss_above is not None and
                    _self.day.realized_pnl <= -halt_on_any_loss_above):
                _self.day.spiral_locked = True
        rb._manage = custom_manage
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
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2), "spirals": spirals,
            "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nOne-Loss-Stop Variants ({len(dates)} days)\n")
configs = [
    ("CURRENT spiral=2",   2, None),
    ("STOP after 1 loss",  1, None),
    ("STOP if loss >$10",  99, 10),
    ("STOP if loss >$20",  99, 20),
    ("STOP if loss >$30",  99, 30),
    ("STOP COMBO 1+>$15",  1, 15),
]

results = []
for cfg in configs:
    r = run_with_threshold(cfg[0], cfg[1], dates, halt_on_any_loss_above=cfg[2])
    results.append(r)
    print(f"{cfg[0]:<22} max_consec={cfg[1]} halt_loss={cfg[2]}: "
          f"trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sp':>3} {'Sharpe':>7}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['spirals']:>2}  {r['sharpe']:>6.2f}")
