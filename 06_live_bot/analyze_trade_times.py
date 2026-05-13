"""Wann am Tag werden Trades getriggert? Power-Hour (9:30-10:30) vs after?"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)
from bot import find_pilot_data_paths
bars_path, _ = find_pilot_data_paths()
if bars_path is None:
    raise FileNotFoundError("pilot data not found at backtest_data/ or 04_backtest/data_pilot/")
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

# Hook detect_bull_flag to capture timestamps of fires
fires = []
_orig_detect = bot_mod.detect_bull_flag

def hooked_detect(bars):
    ok, params = _orig_detect(bars)
    if ok and bars:
        fires.append({
            "time": bars[-1].get("timestamp"),
            "symbol": "?",
            "entry": params["entry_price"],
            "stop": params["stop_price"],
        })
    return ok, params

bot_mod.detect_bull_flag = hooked_detect

# Also capture trade outcomes
trades = []
_orig_manage = bot_mod.ReplayBot._manage

def hooked_manage(self, ts, bar, ny_t):
    pre_pos = ts.in_position
    pre_pnl = self.day.realized_pnl
    result = _orig_manage(self, ts, bar, ny_t)
    if pre_pos and not ts.in_position:
        delta = self.day.realized_pnl - pre_pnl
        # Entry-Time was set on entry bar
        trades.append({
            "symbol": ts.symbol,
            "entry_price": ts.entry_price,
            "pnl": round(delta, 2),
            "exit_time": bar.get("timestamp"),
            "win": delta > 0,
        })
    return result

bot_mod.ReplayBot._manage = hooked_manage

# Need to also capture entry-time — easiest is to hook in ReplayBot.run
# Actually entry-time can be captured via the fires list above
# but we need to correlate. Let me just track entry-time separately.

entry_times = []
_orig_run = bot_mod.ReplayBot.run

def hooked_run(self, target_date):
    bars_path_local, _ = find_pilot_data_paths()
    bars_local = pd.read_parquet(bars_path_local)
    tc_local = next(c for c in bars_local.columns if "time" in c.lower() or "date" in c.lower())
    bars_local[tc_local] = pd.to_datetime(bars_local[tc_local], utc=True)
    return _orig_run(self, target_date)

# Easier: just hook submit_buy in ReplayBot
_orig_submit_buy = bot_mod.ReplayBot.submit_buy

def hooked_submit_buy(self, sym, shares, price):
    # bot.bar's last timestamp via tickers
    ts = self.tickers.get(sym)
    if ts and ts.bars:
        entry_time = ts.bars[-1].get("timestamp")
        entry_times.append({"symbol": sym, "entry_time": entry_time, "price": price})
    return _orig_submit_buy(self, sym, shares, price)

bot_mod.ReplayBot.submit_buy = hooked_submit_buy

for d in dates:
    rb = bot_mod.ReplayBot()
    try:
        rb.run(d.isoformat())
    except Exception:
        continue

# Match entry_times to trades by symbol+price (heuristic)
print(f"\nCaptured {len(entry_times)} entries, {len(trades)} closes")

# Bin by hour bucket
power_hour_trades = []  # 9:30-10:30 ET
mid_morning = []        # 10:30-11:30 ET
late = []              # 11:30+

# Correlate trades to entries by index (assume same order)
n = min(len(entry_times), len(trades))
for i in range(n):
    e = entry_times[i]
    t = trades[i]
    et = e["entry_time"]
    if hasattr(et, "tz_convert"):
        ny = et.tz_convert("America/New_York")
    else:
        try:
            ny = et.astimezone()
        except Exception:
            continue
    minutes_after_open = (ny.hour - 9) * 60 + ny.minute - 30
    bucket = power_hour_trades if minutes_after_open < 60 else (
        mid_morning if minutes_after_open < 120 else late)
    bucket.append({
        "symbol": e["symbol"],
        "minutes_after_open": minutes_after_open,
        "pnl": t["pnl"],
        "win": t["win"],
        "time_ny": ny.strftime("%H:%M"),
    })

print(f"\n{'='*70}")
print(f"By time-bucket:")
print(f"{'='*70}")
for name, bucket in [("Power Hour (9:30-10:30)", power_hour_trades),
                      ("Mid-Morning (10:30-11:30)", mid_morning),
                      ("Late (11:30+)", late)]:
    if not bucket:
        print(f"\n{name}: 0 trades")
        continue
    n = len(bucket)
    pnl = sum(x["pnl"] for x in bucket)
    wins = sum(1 for x in bucket if x["win"])
    print(f"\n{name}: {n} trades, ${pnl:+.2f} PnL, "
          f"{wins}/{n} wins = {wins/n*100:.0f}% win-rate")
    print(f"  Avg PnL/trade: ${pnl/n:+.2f}")
    for x in bucket[:5]:
        wl = "W" if x["win"] else "L"
        print(f"    {x['time_ny']} ({wl}) ${x['pnl']:+6.2f}  {x['symbol']}")
