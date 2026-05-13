"""Targeted: why does CNCK bar #32 with 9.52% pole fail?"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf, pandas as pd, numpy as np
import bot as bot_mod
from bar_aggregator import BarAggregator

df = yf.download("CNCK", period="1d", interval="1m", progress=False, auto_adjust=False)
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
df = df.reset_index()
df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
ts_col = next((c for c in ["datetime", "date"] if c in df.columns), None)

agg = BarAggregator(5)
bars5 = []
for _, row in df.iterrows():
    out = agg.add("CNCK", {"open": float(row["open"]), "high": float(row["high"]),
                              "low": float(row["low"]), "close": float(row["close"]),
                              "volume": float(row["volume"]), "timestamp": row[ts_col]})
    if out:
        bars5.append(out)

print(f"Total 5-min bars: {len(bars5)}")

# Show bars around #32
for i in range(28, min(40, len(bars5))):
    b = bars5[i]
    g = "G" if b["close"] > b["open"] else "R"
    print(f"  #{i} {b['timestamp']} O={b['open']:.2f} H={b['high']:.2f} "
          f"L={b['low']:.2f} C={b['close']:.2f} V={b['volume']:.0f} [{g}]")

# Now debug bar 32 in detail
print(f"\n=== BAR #32 in detail ===")
i = 32
bars = bars5[:33]
o = np.array([x["open"] for x in bars])
h = np.array([x["high"] for x in bars])
l = np.array([x["low"] for x in bars])
c = np.array([x["close"] for x in bars])
v = np.array([x["volume"] for x in bars])
green = c > o
rng = np.maximum(h - l, 1e-9)
upper_wick = h - np.maximum(c, o)
topping = upper_wick / rng

# Bar 32 itself
print(f"  i={i} green={green[i]} close={c[i]:.2f}")
vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
print(f"  vol[{i}]={v[i]:.0f}  vol_sma[{i}]={vol_sma[i]:.0f}  "
      f"req >= {vol_sma[i]*1.5:.0f}  pass={v[i] >= vol_sma[i]*1.5}")

# Try pole=3 flag=1
fl = 1; pl = 3
ps = i - fl - pl  # 28
pe = i - fl       # 31
print(f"\n  Trying pole={pl} flag={fl}:")
print(f"    ps={ps} pe={pe} (pole bars 28-30, flag bar 31, breakout=32)")
print(f"    green pole: {green[ps:pe]} → all={green[ps:pe].all()}")
print(f"    pole start o={o[ps]:.2f}, pole end c={c[pe-1]:.2f}")
p_pct = (c[pe-1] - o[ps]) / o[ps] * 100
print(f"    pole_pct={p_pct:.2f}%  threshold={bot_mod.POLE_MIN_MOVE_PCT}")
print(f"    topping_max={topping[ps:pe].max():.3f}  threshold<={bot_mod.POLE_TOPPING_TAIL_MAX}")
# vol rising
if pl >= 4:
    fh = v[ps:ps+pl//2].mean()
    sh = v[ps+pl//2:pe].mean()
    print(f"    vol rising: first_half={fh:.0f} second_half={sh:.0f} pass={sh >= fh*0.9}")
else:
    print(f"    vol rising: skipped (pl<4)")
# flag retrace
fs = pe; fe = i
p_h = c[pe-1] - o[ps]
fl_low = l[fs:fe].min()
retrace = (c[pe-1] - fl_low) / p_h * 100
print(f"    flag_low={fl_low:.2f} retrace_pct={retrace:.1f}%  threshold<={bot_mod.FLAG_RETRACE_MAX_PCT}")
# breakout
prh = h[fs:fe].max()
print(f"    flag_high={prh:.2f} bar_high={h[i]:.2f} pass={h[i] > prh}")
ep = prh + bot_mod.SLIPPAGE_CENTS
sp = fl_low - bot_mod.SLIPPAGE_CENTS
print(f"    entry={ep:.2f} stop={sp:.2f} pass={ep > sp}")

# Final: VWAP/MACD/FBO vetos
from vwap_filter import is_above_vwap
from indicators import macd_is_bullish, false_breakout_veto
print(f"\n  Vetos:")
print(f"    VWAP: is_above={is_above_vwap(bars, c[i])}")
print(f"    MACD: bullish={macd_is_bullish(c.tolist())}")
fbo_veto, fbo_why = false_breakout_veto(bars)
print(f"    FBO: vetoed={fbo_veto} ({fbo_why})")
