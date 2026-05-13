"""replay_today.py — pull TODAY's intraday bars for the 10 watchlist symbols
and stream them through the full bot pattern-detection + veto pipeline,
logging EVERY filter rejection so we see exactly why no trades fire.

Usage:
  python replay_today.py SYMBOL1 SYMBOL2 ...
"""
from __future__ import annotations
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import yfinance as yf
import pandas as pd

import bot as bot_mod
from indicators import macd_is_bullish, false_breakout_veto
from vwap_filter import is_above_vwap

# Counters per ticker
class FilterStats:
    def __init__(self):
        self.bars_seen = 0
        self.too_few_bars = 0
        self.red_bar = 0
        self.price_out_of_range = 0
        self.low_volume = 0
        self.no_pole = 0
        self.no_flag = 0
        self.veto_vwap = 0
        self.veto_macd = 0
        self.veto_fbo = 0
        self.veto_pump_dump = 0
        self.veto_can_enter = 0
        self.size_zero = 0
        self.entries = 0
        self.entry_details = []  # (bar_idx, entry, stop, t2)


def analyze_symbol(symbol: str, intraday_pct: float, rvol: float) -> FilterStats:
    """Pull today's 1-min bars und stream through detect_bull_flag."""
    print(f"\n{'='*60}")
    print(f"ANALYZE {symbol}  (daily +{intraday_pct:.1f}%, RVOL {rvol:.1f}x)")
    print(f"{'='*60}")

    # Pull today's 1-min bars
    try:
        df = yf.download(symbol, period="1d", interval="1m",
                          progress=False, auto_adjust=False)
        if df.empty:
            print(f"  NO INTRADAY DATA for {symbol}")
            return FilterStats()
    except Exception as e:
        print(f"  YFINANCE ERROR: {e}")
        return FilterStats()

    # Handle multi-index columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()  # bring DatetimeIndex to column
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]

    timestamp_col = next((c for c in ["datetime", "date"] if c in df.columns), None)
    if timestamp_col is None:
        print(f"  NO TIMESTAMP COLUMN: {list(df.columns)}")
        return FilterStats()

    print(f"  Pulled {len(df)} 1-min bars")
    print(f"  Time range: {df[timestamp_col].iloc[0]} - {df[timestamp_col].iloc[-1]}")

    stats = FilterStats()
    bars = []  # rolling window

    # Need DayState for can_enter_new context
    day = bot_mod.DayState()
    day.quarter_size_unlocked = True  # assume full size for diagnostic
    day.spy_size_multiplier = 1.0

    for idx, row in df.iterrows():
        # Build bar dict
        bar = {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "timestamp": row[timestamp_col],
        }
        bars.append(bar)
        stats.bars_seen += 1

        if len(bars) < bot_mod.POLE_MIN_CANDLES + bot_mod.FLAG_MIN_CANDLES + 5:
            stats.too_few_bars += 1
            continue

        # Direct call to detect_bull_flag — returns signal + reason
        # But we want to know WHICH filter blocked. Let's do it step by step.
        i = len(bars) - 1
        b_last = bars[i]

        # 1) Red breakout bar
        if b_last["close"] <= b_last["open"]:
            stats.red_bar += 1
            continue
        # 2) Price out of range
        if b_last["close"] < bot_mod.PRICE_MIN or b_last["close"] > bot_mod.PRICE_MAX:
            stats.price_out_of_range += 1
            continue
        # 3) Volume
        vols = [b["volume"] for b in bars[-20:]]
        avg_vol = sum(vols) / len(vols) if vols else 0
        if avg_vol <= 0 or b_last["volume"] < avg_vol * bot_mod.BREAKOUT_VOL_FACTOR:
            stats.low_volume += 1
            continue

        # 4) Full pattern detection
        signal, params = bot_mod.detect_bull_flag(bars)
        if not signal:
            why = params.get("_veto", "no_pattern")
            if "vwap" in why:
                stats.veto_vwap += 1
            elif "macd" in why:
                stats.veto_macd += 1
            elif "fbo" in why:
                stats.veto_fbo += 1
            else:
                stats.no_pole += 1  # mostly: pole/flag conditions didn't match
            continue

        # We have a signal! Now check entry conditions
        # Compute position size
        equity = 25_000.0  # paper-account approx
        shares = bot_mod.compute_position_size(
            params["entry_price"], params["stop_price"], equity, day,
            ny_time=None  # skip power-hour
        )
        if shares < 1:
            stats.size_zero += 1
            continue

        # SUCCESSFUL ENTRY
        stats.entries += 1
        stats.entry_details.append({
            "bar_idx": i,
            "time": b_last["timestamp"],
            "entry": params["entry_price"],
            "stop": params["stop_price"],
            "target2": params["target2"],
            "shares": shares,
        })

    # Print summary
    print(f"\n  STATS for {symbol}:")
    print(f"    Total bars:        {stats.bars_seen}")
    print(f"    Too few bars:      {stats.too_few_bars}")
    print(f"    Red breakout bar:  {stats.red_bar}")
    print(f"    Price out range:   {stats.price_out_of_range}")
    print(f"    Low volume:        {stats.low_volume}")
    print(f"    No pole/flag:      {stats.no_pole}")
    print(f"    VWAP veto:         {stats.veto_vwap}")
    print(f"    MACD veto:         {stats.veto_macd}")
    print(f"    FBO veto:          {stats.veto_fbo}")
    print(f"    Size=0:            {stats.size_zero}")
    print(f"    >>> ENTRIES:       {stats.entries}")
    for e in stats.entry_details[:5]:
        print(f"        @ {e['time']}: entry=${e['entry']:.2f} "
              f"stop=${e['stop']:.2f} T2=${e['target2']:.2f} shares={e['shares']}")
    return stats


if __name__ == "__main__":
    # Default: heutige Top-10
    default = [
        ("TDIC", 176.0, 14.0),
        ("BWEN", 126.6, 16.4),
        ("NYC", 66.3, 17.5),
        ("CNCK", 37.1, 19.6),
        ("STAK", 89.2, 6.6),
        ("WOK", 87.0, 6.4),
        ("HTCO", 71.9, 5.3),
        ("MGNX", 30.2, 9.0),
        ("QUBT", 41.9, 5.9),
        ("VSTS", 34.0, 6.0),
    ]
    syms = sys.argv[1:] if len(sys.argv) > 1 else [s[0] for s in default]
    pct_map = {s[0]: s[1] for s in default}
    rvol_map = {s[0]: s[2] for s in default}

    totals = FilterStats()
    by_symbol = {}
    for sym in syms:
        s = analyze_symbol(sym, pct_map.get(sym, 0), rvol_map.get(sym, 0))
        by_symbol[sym] = s
        totals.bars_seen += s.bars_seen
        totals.too_few_bars += s.too_few_bars
        totals.red_bar += s.red_bar
        totals.price_out_of_range += s.price_out_of_range
        totals.low_volume += s.low_volume
        totals.no_pole += s.no_pole
        totals.veto_vwap += s.veto_vwap
        totals.veto_macd += s.veto_macd
        totals.veto_fbo += s.veto_fbo
        totals.size_zero += s.size_zero
        totals.entries += s.entries

    print(f"\n\n{'='*60}")
    print("AGGREGATE — ALL SYMBOLS")
    print(f"{'='*60}")
    print(f"  Bars analyzed:     {totals.bars_seen}")
    print(f"  Pattern candidates rejected by:")
    print(f"    Too few bars:    {totals.too_few_bars}")
    print(f"    Red bar:         {totals.red_bar}")
    print(f"    Price range:     {totals.price_out_of_range}")
    print(f"    Low volume:      {totals.low_volume}")
    print(f"    No pole/flag:    {totals.no_pole}")
    print(f"    VWAP veto:       {totals.veto_vwap}")
    print(f"    MACD veto:       {totals.veto_macd}")
    print(f"    FBO veto:        {totals.veto_fbo}")
    print(f"    Size=0:          {totals.size_zero}")
    print(f"  TOTAL ENTRIES:     {totals.entries}")
    print(f"\nPer-symbol entry count:")
    for sym, s in by_symbol.items():
        marker = "OK" if s.entries > 0 else "--"
        print(f"  {marker} {sym}: {s.entries} entries  (bars={s.bars_seen})")
