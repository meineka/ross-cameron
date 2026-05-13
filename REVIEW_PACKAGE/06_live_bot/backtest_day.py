"""Backtest: Pattern-Detection auf einem historischen Tag.

Pullt 1-Min-Bars von Alpaca für gegebene Symbole + Datum, läuft
detect_bull_flag bei jedem Bar und meldet Würde-getradet-Events.

Nutzung:
  python backtest_day.py 2026-05-11 ODYS,WOK,STFS
  python backtest_day.py 2026-05-11   # default: alle gestern bekannten Setups
"""
from __future__ import annotations
import sys, io, os
from pathlib import Path
from datetime import datetime, timezone, time as dtime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from secrets_loader import get_alpaca_keys
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Pattern-Detection aus bot.py importieren
import bot as bot_mod
# Audit-Iter 33 (Bug BT-1): 1-min → 5-min Aggregation (Option A consistency)
from bar_aggregator import BarAggregator

KEY, SEC = get_alpaca_keys()
dc = StockHistoricalDataClient(KEY, SEC)


def fetch_bars(symbols: list[str], date: str) -> dict[str, list[dict]]:
    """1-min bars für gegebenen Tag (ganztags inkl. Pre-Market)."""
    start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    end = start.replace(hour=23, minute=59)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=start, end=end,
    )
    try:
        data = dc.get_stock_bars(req).data
    except Exception as e:
        print(f"FAIL fetching bars: {e}")
        return {}
    out = {}
    for sym in symbols:
        if sym not in data: continue
        out[sym] = [{
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close),
            "volume": int(b.volume), "ts": b.timestamp,
        } for b in data[sym]]
    return out


def replay(symbol: str, bars: list[dict]):
    """Roll-forward: 1-min bars → 5-min aggregated → detect_bull_flag.

    Audit-Iter 33 (Bug BT-1): vor dem Fix wurde detect_bull_flag direkt auf
    1-min bars gerufen — 0 triggers fast immer weil POLE_MIN_MOVE_PCT=5%
    auf 1-min praktisch nie erreicht wird (Cameron-Pattern ist 5-min).
    Jetzt: Aggregator wie im live bot post-Option-A.
    """
    if len(bars) < 25:  # need ~5 5-min-bars minimum
        print(f"  {symbol}: nur {len(bars)} 1-min bars, skip")
        return []
    agg = BarAggregator(bucket_minutes=bot_mod.BAR_AGGREGATION_MINUTES)
    bars5 = []  # accumulated 5-min bars
    triggers = []
    # Map 5-min bar back to its triggering 1-min bar_idx (= last 1-min bar
    # of next bucket = bar idx where emit happened)
    for i, b1 in enumerate(bars):
        bar1m = {
            "open": b1["open"], "high": b1["high"], "low": b1["low"],
            "close": b1["close"], "volume": b1["volume"],
            "timestamp": b1["ts"],
        }
        bar5m = agg.add(symbol, bar1m)
        if bar5m is None:
            continue
        bars5.append(bar5m)
        if len(bars5) < bot_mod.POLE_MIN_CANDLES + bot_mod.FLAG_MIN_CANDLES + 5:
            continue
        ok, params = bot_mod.detect_bull_flag(bars5)
        if ok:
            triggers.append({
                "time": bar5m["timestamp"],
                "entry": params["entry_price"],
                "stop": params["stop_price"],
                "t1": params["target1"],
                "t2": params["target2"],
                "bar_idx": i,  # 1-min bar idx (für simulate_outcome)
            })
    return triggers


def simulate_outcome(bars: list[dict], trig: dict) -> dict:
    """Was hätte der Trade gemacht? Bracket-Stop + T2-TP simulieren."""
    entry_idx = trig["bar_idx"]
    entry = trig["entry"]
    stop = trig["stop"]
    t2 = trig["t2"]
    t1 = trig["t1"]
    # Bars NACH entry durchgehen
    for j in range(entry_idx + 1, min(entry_idx + 60, len(bars))):  # max 60 bars hold
        b = bars[j]
        if b["low"] <= stop:
            return {"exit": stop, "reason": "stop", "bars_held": j - entry_idx,
                    "pnl_per_share": stop - entry}
        if b["high"] >= t2:
            return {"exit": t2, "reason": "T2", "bars_held": j - entry_idx,
                    "pnl_per_share": t2 - entry}
    # End of day
    return {"exit": bars[-1]["close"], "reason": "EOD",
            "bars_held": len(bars) - entry_idx,
            "pnl_per_share": bars[-1]["close"] - entry}


def main():
    if len(sys.argv) < 2:
        print("usage: backtest_day.py YYYY-MM-DD [SYM,SYM,...]")
        sys.exit(1)
    date = sys.argv[1]
    if len(sys.argv) >= 3:
        symbols = sys.argv[2].split(",")
    else:
        # Default: heute's known movers von Cameron's Video
        symbols = ["ODYS", "WOK", "STFS", "CLIK", "HSPT", "INBS", "MEX", "CLK"]

    print("=" * 70)
    print(f"BACKTEST {date} — {len(symbols)} symbols")
    print("=" * 70)
    print(f"Fetching 1-min bars...")
    bars_by_sym = fetch_bars(symbols, date)
    print(f"  Got data for {len(bars_by_sym)} symbols\n")

    all_triggers = []
    for sym, bars in bars_by_sym.items():
        print(f"--- {sym} ({len(bars)} bars) ---")
        triggers = replay(sym, bars)
        if not triggers:
            print(f"  no triggers")
            continue
        # Audit-Iter 33 (Bug BT-5): vorher truncated nach :5 — Summary war
        # nur auf erste 5 trigger berechnet. Jetzt: alle triggers analysiert,
        # nur erste 5 detailliert ausgegeben.
        for idx, t in enumerate(triggers):
            outcome = simulate_outcome(bars, t)
            all_triggers.append({"sym": sym, **t, **outcome})
            if idx < 5:
                print(f"  TRIGGER @ {t['time'].strftime('%H:%M')}  entry ${t['entry']:.2f}  stop ${t['stop']:.2f}  T2 ${t['t2']:.2f}")
                print(f"    -> exit @ ${outcome['exit']:.2f} ({outcome['reason']}, {outcome['bars_held']} bars)  PnL/share ${outcome['pnl_per_share']:+.2f}")
        if len(triggers) > 5:
            print(f"  ... + {len(triggers)-5} more triggers (all included in summary)")

    print("\n" + "=" * 70)
    print(f"SUMMARY")
    print("=" * 70)
    print(f"Total triggers: {len(all_triggers)}")
    wins = sum(1 for t in all_triggers if t["pnl_per_share"] > 0)
    losses = sum(1 for t in all_triggers if t["pnl_per_share"] < 0)
    flat = sum(1 for t in all_triggers if t["pnl_per_share"] == 0)
    print(f"  Wins:   {wins}")
    print(f"  Losses: {losses}")
    print(f"  Flat:   {flat}")
    if all_triggers:
        total = sum(t["pnl_per_share"] for t in all_triggers)
        avg = total / len(all_triggers)
        print(f"  Sum PnL/share: ${total:+.2f}")
        print(f"  Avg PnL/share: ${avg:+.3f}")
        print(f"  Best:  ${max(t['pnl_per_share'] for t in all_triggers):+.2f}")
        print(f"  Worst: ${min(t['pnl_per_share'] for t in all_triggers):+.2f}")


if __name__ == "__main__":
    main()
