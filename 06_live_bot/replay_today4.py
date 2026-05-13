"""replay_today4.py — Granular reject-breakdown auf 5-Min-Aggregated bars."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf, pandas as pd, numpy as np
import bot as bot_mod
from bar_aggregator import BarAggregator


def analyze(symbol):
    print(f"\n=== {symbol} ===")
    df = yf.download(symbol, period="1d", interval="1m",
                      progress=False, auto_adjust=False)
    if df.empty:
        print("no data"); return
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    ts_col = next((c for c in ["datetime", "date"] if c in df.columns), None)

    agg = BarAggregator(bucket_minutes=5)
    bars5 = []
    for _, row in df.iterrows():
        bar1 = {"open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row["volume"]), "timestamp": row[ts_col]}
        out = agg.add(symbol, bar1)
        if out:
            bars5.append(out)
    print(f"5-min bars: {len(bars5)}")

    # Stats
    too_few = red = price = lowvol = nopole = veto_vwap = veto_macd = veto_fbo = 0
    pole_pcts = []
    flag_retraces = []
    for n in range(12, len(bars5)+1):
        bars = bars5[:n]
        i = n - 1
        b = bars[i]
        if b["close"] <= b["open"]:
            red += 1; continue
        if b["close"] < bot_mod.PRICE_MIN or b["close"] > bot_mod.PRICE_MAX:
            price += 1; continue
        vols = [x["volume"] for x in bars[-20:]]
        avg_v = sum(vols)/len(vols) if vols else 0
        if avg_v <= 0 or b["volume"] < avg_v * bot_mod.BREAKOUT_VOL_FACTOR:
            lowvol += 1; continue
        signal, params = bot_mod.detect_bull_flag(bars)
        if not signal:
            why = params.get("_veto", "")
            if "vwap" in why: veto_vwap += 1
            elif "macd" in why: veto_macd += 1
            elif "fbo" in why: veto_fbo += 1
            else:
                nopole += 1
                # show pole stats for first 5 failures
                if nopole <= 5:
                    # try to find what poles existed
                    o = np.array([x["open"] for x in bars])
                    h = np.array([x["high"] for x in bars])
                    l = np.array([x["low"] for x in bars])
                    c = np.array([x["close"] for x in bars])
                    green = c > o
                    found_poles = []
                    for fl in range(1, 4):
                        for pl in range(3, 8):
                            ps = i - fl - pl; pe = i - fl
                            if ps < 0: continue
                            if not green[ps:pe].all(): continue
                            p_start = o[ps]; p_end = c[pe-1]
                            if p_start <= 0: continue
                            p_pct = (p_end - p_start) / p_start * 100
                            found_poles.append((pl, fl, p_pct))
                    if found_poles:
                        print(f"  bar #{i}: poles found but rejected:")
                        for pl, fl, p in found_poles[:3]:
                            print(f"    pole={pl} flag={fl} move={p:.2f}%  threshold={bot_mod.POLE_MIN_MOVE_PCT}%")
                    else:
                        print(f"  bar #{i}: NO all-green pole found")
            continue
        print(f"  *** PATTERN FIRED bar #{i}")
    print(f"  total: red={red} price={price} lowvol={lowvol} "
          f"nopole={nopole} veto_vwap={veto_vwap} veto_macd={veto_macd} veto_fbo={veto_fbo}")


for s in ["BWEN", "CNCK", "QUBT"]:
    analyze(s)
