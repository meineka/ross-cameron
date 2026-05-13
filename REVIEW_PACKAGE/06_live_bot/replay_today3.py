"""replay_today3.py — Verify Option A: 1-Min bars → 5-Min aggregator →
Pattern-Detection. Erwarte JETZT Entries auf den Top-10 Tickern.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf
import pandas as pd

import bot as bot_mod
from bar_aggregator import BarAggregator


def replay_symbol_with_aggregator(symbol: str):
    print(f"\n{'='*70}")
    print(f"5-MIN-AGGREGATED REPLAY: {symbol}")
    print(f"{'='*70}")
    try:
        df = yf.download(symbol, period="1d", interval="1m",
                          progress=False, auto_adjust=False)
        if df.empty:
            print("  NO DATA"); return 0
    except Exception as e:
        print(f"  ERR {e}"); return 0

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    ts_col = next((c for c in ["datetime", "date"] if c in df.columns), None)
    print(f"  {len(df)} 1-min bars")

    agg = BarAggregator(bucket_minutes=5)
    bars5 = []  # accumulated 5-min bars
    entries = []

    day = bot_mod.DayState()
    day.quarter_size_unlocked = True
    day.spy_size_multiplier = 1.0

    for _, row in df.iterrows():
        bar_1m = {
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
            "timestamp": row[ts_col],
        }
        bar_5m = agg.add(symbol, bar_1m)
        if bar_5m is None:
            continue
        bars5.append(bar_5m)
        if len(bars5) < bot_mod.POLE_MIN_CANDLES + bot_mod.FLAG_MIN_CANDLES + 5:
            continue
        signal, params = bot_mod.detect_bull_flag(bars5)
        if not signal:
            continue
        # Size check (skipping ny_time complexity)
        shares = bot_mod.compute_position_size(
            params["entry_price"], params["stop_price"], 25_000, day
        )
        if shares < 1:
            continue
        entries.append({
            "time": bar_5m["timestamp"],
            "entry": params["entry_price"],
            "stop": params["stop_price"],
            "t2": params["target2"],
            "shares": shares,
        })

    print(f"  Aggregated bars: {len(bars5)}")
    print(f"  Entries fired:   {len(entries)}")
    for e in entries[:5]:
        risk_per_share = e["entry"] - e["stop"]
        max_loss = risk_per_share * e["shares"]
        max_gain = (e["t2"] - e["entry"]) * e["shares"]
        print(f"    @ {e['time']}: BUY {e['shares']} @ ${e['entry']:.2f}  "
              f"STOP ${e['stop']:.2f}  T2 ${e['t2']:.2f}  "
              f"risk -${max_loss:.2f}  reward +${max_gain:.2f}")
    return len(entries)


if __name__ == "__main__":
    syms = ["BWEN", "NYC", "CNCK", "STAK", "WOK", "HTCO", "MGNX", "QUBT", "VSTS"]
    total = 0
    by_sym = {}
    for s in syms:
        n = replay_symbol_with_aggregator(s)
        total += n
        by_sym[s] = n
    print(f"\n{'='*70}")
    print(f"AGGREGATE: {total} entries across {len(syms)} symbols")
    print(f"{'='*70}")
    for sym, n in by_sym.items():
        marker = "OK" if n > 0 else "--"
        print(f"  {marker} {sym}: {n} entries")
