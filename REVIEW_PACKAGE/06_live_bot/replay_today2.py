"""replay_today2.py — detailed pattern-rejection breakdown.

Bei "no_pole/flag": warum genau hat detect_bull_flag rejected?
Loggt die ersten N kandidaten-bars und zeigt was an pole/flag scheiterte.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf
import pandas as pd
import numpy as np

import bot as bot_mod


def deep_analyze(symbol: str):
    print(f"\n{'='*70}")
    print(f"DEEP {symbol}")
    print(f"{'='*70}")
    try:
        df = yf.download(symbol, period="1d", interval="1m",
                          progress=False, auto_adjust=False)
        if df.empty:
            print("  NO DATA"); return
    except Exception as e:
        print(f"  ERR {e}"); return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    ts_col = next((c for c in ["datetime", "date"] if c in df.columns), None)

    bars = []
    pole_pct_seen = []
    pole_topping_seen = []
    flag_retrace_seen = []
    pole_vol_rising_fail = 0
    breakout_below_flag_high = 0

    for _, row in df.iterrows():
        bar = {
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
            "timestamp": row[ts_col],
        }
        bars.append(bar)
        if len(bars) < 12:
            continue

        # Replicate detect_bull_flag's pole/flag scanning
        o = np.array([b["open"] for b in bars])
        h = np.array([b["high"] for b in bars])
        l = np.array([b["low"] for b in bars])
        c = np.array([b["close"] for b in bars])
        v = np.array([b["volume"] for b in bars])
        green = c > o
        rng = np.maximum(h - l, 1e-9)
        upper_wick = h - np.maximum(c, o)
        topping = upper_wick / rng

        i = len(bars) - 1
        if not green[i]:
            continue
        if c[i] < bot_mod.PRICE_MIN or c[i] > bot_mod.PRICE_MAX:
            continue
        vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
        if np.isnan(vol_sma[i]) or vol_sma[i] <= 0:
            continue
        if v[i] < vol_sma[i] * bot_mod.BREAKOUT_VOL_FACTOR:
            continue

        # Try pole+flag combinations
        for fl in range(bot_mod.FLAG_MIN_CANDLES, bot_mod.FLAG_MAX_CANDLES + 1):
            for pl in range(bot_mod.POLE_MIN_CANDLES, bot_mod.POLE_MAX_CANDLES + 1):
                ps = i - fl - pl
                pe = i - fl
                if ps < 0:
                    continue
                if not green[ps:pe].all():
                    continue  # pole nicht durchgehend green
                p_start = o[ps]; p_end = c[pe-1]
                if p_start <= 0:
                    continue
                p_pct = (p_end - p_start) / p_start * 100
                pole_pct_seen.append(p_pct)
                if p_pct < bot_mod.POLE_MIN_MOVE_PCT:
                    continue
                topping_max = topping[ps:pe].max()
                pole_topping_seen.append(topping_max)
                if topping_max > bot_mod.POLE_TOPPING_TAIL_MAX:
                    continue
                if bot_mod.POLE_VOLUME_RISING_REQUIRED and pl >= 4:
                    fh = v[ps:ps+pl//2].mean()
                    sh = v[ps+pl//2:pe].mean()
                    if sh < fh * 0.9:
                        pole_vol_rising_fail += 1
                        continue
                fs = pe; fe = i
                p_h = p_end - p_start
                if p_h <= 0:
                    continue
                fl_low = l[fs:fe].min()
                if fl_low > p_end:  # fixed
                    continue
                retrace_pct = (p_end - fl_low) / p_h * 100
                flag_retrace_seen.append(retrace_pct)
                if retrace_pct > bot_mod.FLAG_RETRACE_MAX_PCT:
                    continue
                prh = h[fs:fe].max()
                if h[i] <= prh:
                    breakout_below_flag_high += 1
                    continue
                ep = prh + bot_mod.SLIPPAGE_CENTS
                sp = fl_low - bot_mod.SLIPPAGE_CENTS
                if ep <= sp:
                    continue
                # signal would fire here (before vetos)
                print(f"  *** PATTERN ARMED bar #{i} pole={pl} flag={fl} "
                      f"pole_pct={p_pct:.1f}% retrace={retrace_pct:.0f}% "
                      f"entry=${ep:.2f} stop=${sp:.2f}")
                return  # show first only

    print(f"  No pole/flag fired across {len(bars)} bars.")
    if pole_pct_seen:
        print(f"  pole_pct stats: count={len(pole_pct_seen)} "
              f"max={max(pole_pct_seen):.1f}% "
              f"avg={sum(pole_pct_seen)/len(pole_pct_seen):.1f}% "
              f"threshold>{bot_mod.POLE_MIN_MOVE_PCT}%")
        above_threshold = sum(1 for p in pole_pct_seen if p >= bot_mod.POLE_MIN_MOVE_PCT)
        print(f"    poles meeting move-threshold: {above_threshold}/{len(pole_pct_seen)}")
    else:
        print(f"  NO valid pole found (all-green pole > {bot_mod.POLE_MIN_CANDLES} bars).")
    if pole_topping_seen:
        above = sum(1 for t in pole_topping_seen if t > bot_mod.POLE_TOPPING_TAIL_MAX)
        print(f"  pole_topping_tail rejections: {above}/{len(pole_topping_seen)} "
              f"(threshold > {bot_mod.POLE_TOPPING_TAIL_MAX})")
    if flag_retrace_seen:
        above = sum(1 for r in flag_retrace_seen if r > bot_mod.FLAG_RETRACE_MAX_PCT)
        print(f"  flag_retrace rejections: {above}/{len(flag_retrace_seen)} "
              f"(threshold > {bot_mod.FLAG_RETRACE_MAX_PCT}%)")
    print(f"  pole_volume_rising fails: {pole_vol_rising_fail}")
    print(f"  breakout_below_flag_high: {breakout_below_flag_high}")


if __name__ == "__main__":
    syms = ["BWEN", "CNCK", "WOK", "QUBT", "VSTS"]  # high-bar-count subset
    for s in syms:
        deep_analyze(s)
