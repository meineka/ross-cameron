"""Diagnose was die 13 winning trades charakterisiert.
Print pro Trade: date, ticker, entry-price, entry-time, hold-mins, pnl-cents/share.
Suche Pattern für nächste Iter-Hypothese.
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)

# Instrument submit_buy + _manage exits to capture trade-level data
captured = []

_orig_submit = bot_mod.ReplayBot.submit_buy

def patched_submit(self, sym, qty, price):
    captured.append({
        "type": "ENTRY", "sym": sym, "qty": qty, "price": price,
        "ts": getattr(self, "_current_ny_t", None)})
    return _orig_submit(self, sym, qty, price)
bot_mod.ReplayBot.submit_buy = patched_submit

# Hook _manage to track current ny_t
_orig_manage = bot_mod.ReplayBot._manage
def patched_manage(self, ts, bar, ny_t):
    self._current_ny_t = ny_t
    pnl_before = self.day.realized_pnl
    in_pos_before = ts.in_position
    res = _orig_manage(self, ts, bar, ny_t)
    if in_pos_before and not ts.in_position:
        pnl_delta = round(self.day.realized_pnl - pnl_before, 2)
        captured.append({"type": "EXIT", "sym": ts.symbol,
                         "ts": ny_t, "pnl": pnl_delta,
                         "entry": ts.entry_price,
                         "stop": ts.stop_price,
                         "t1": ts.target1_price,
                         "t2": ts.target2_price,
                         "shares": ts.initial_shares})
    return res
bot_mod.ReplayBot._manage = patched_manage


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

for d in dates:
    rb = bot_mod.ReplayBot()
    try:
        rb.run(d.isoformat())
    except Exception:
        continue

# Pair entries with exits
print(f"{'date':<12}{'sym':<8}{'entry$':>9}{'shares':>8}{'risk%':>7}"
      f"{'t1$':>8}{'t2$':>8}{'pnl$':>9}{'time':>10}")
print("-"*80)
entries = [c for c in captured if c["type"] == "ENTRY"]
exits = [c for c in captured if c["type"] == "EXIT"]
for ent, ex in zip(entries, exits):
    risk_pct = round((ex["entry"] - ex["stop"]) / ex["entry"] * 100, 2)
    t1_r = round((ex["t1"] - ex["entry"]) / (ex["entry"] - ex["stop"]), 2)
    t2_r = round((ex["t2"] - ex["entry"]) / (ex["entry"] - ex["stop"]), 2)
    print(f"          {ex['sym']:<8}{ex['entry']:>9.2f}{ex['shares']:>8d}"
          f"{risk_pct:>7.2f}{ex['t1']:>8.2f}{ex['t2']:>8.2f}"
          f"{ex['pnl']:>+9.2f}{str(ex['ts'])[:8]:>10}")
    print(f"           t1R={t1_r}  t2R={t2_r}")
