"""Analyze: which trades happened in pilot backtest, and what was their
entry-price + adverse-move-when-stopped? Used to validate
adaptive quick-exit hypothesis."""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)
bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

# Patch ReplayBot to capture trade-details
trades = []
_orig_manage = bot_mod.ReplayBot._manage
_orig_submit = None

def patched_manage(self, ts, bar, ny_t):
    pre_pnl = self.day.realized_pnl
    pre_pos = ts.in_position
    result = _orig_manage(self, ts, bar, ny_t)
    if pre_pos and not ts.in_position:
        # Trade closed
        delta = self.day.realized_pnl - pre_pnl
        trades.append({
            "symbol": ts.symbol,
            "entry": ts.entry_price,
            "stop": ts.stop_price,
            "shares": ts.initial_shares,
            "pnl": round(delta, 2),
            "exit_bar_close": bar["close"],
            "win": delta > 0,
        })
    return result

bot_mod.ReplayBot._manage = patched_manage

for d in dates:
    rb = bot_mod.ReplayBot()
    try:
        rb.run(d.isoformat())
    except Exception:
        continue

print(f"\nAll {len(trades)} pilot trades:\n")
print(f"{'Symbol':<7} {'Entry':>7} {'Stop':>7} {'Risk%':>7} {'Exit':>7} {'Move%':>7} {'PnL':>8} {'W/L':>4}")
print("-" * 70)
for t in trades:
    risk_pct = (t["entry"] - t["stop"]) / t["entry"] * 100
    move_pct = (t["exit_bar_close"] - t["entry"]) / t["entry"] * 100
    wl = "W" if t["win"] else "L"
    print(f"{t['symbol']:<7} ${t['entry']:>6.2f} ${t['stop']:>6.2f} "
          f"{risk_pct:>6.1f}% ${t['exit_bar_close']:>6.2f} {move_pct:>+6.1f}% "
          f"${t['pnl']:>+6.2f}   {wl}")

# Stats by price-tier
print(f"\nBy price tier:")
for lo, hi in [(0, 5), (5, 10), (10, 20)]:
    tier = [t for t in trades if lo <= t["entry"] < hi]
    if not tier: continue
    avg_risk_pct = sum((t["entry"]-t["stop"])/t["entry"]*100 for t in tier) / len(tier)
    wins = sum(1 for t in tier if t["win"])
    print(f"  ${lo}-${hi}: {len(tier)} trades, avg risk={avg_risk_pct:.1f}%, "
          f"win-rate {wins}/{len(tier)} = {wins/len(tier)*100:.0f}%")

# Did 30c-quick-exit fire? In replay it doesn't (replay has no quick_exit)
# But check: what would 30c-against vs 3%-against look like for losers?
print(f"\nIf 30c-quick-exit vs 3%-adaptive would have fired:")
print(f"{'Symbol':<7} {'Entry':>7} {'30c-abs':>8} {'3%-pct':>8} {'Note'}")
for t in trades:
    if not t["win"]:
        pct3 = t["entry"] * 0.03
        print(f"  {t['symbol']:<7} ${t['entry']:>6.2f} ${0.30:>6.2f}  ${pct3:>6.2f}  "
              f"{'3% TIGHTER' if pct3 < 0.30 else '30c TIGHTER'}")
