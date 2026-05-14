"""Fetch older pilot days via Alpaca (geht weiter zurück als yfinance's 60d-cap).

Target: 2026-02-15 to 2026-03-15 (one extra month before existing pilot).
"""
from __future__ import annotations
import os
import sys
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

PILOT_DIR = Path(__file__).resolve().parents[1] / "04_backtest" / "data_pilot"
BARS_PATH = PILOT_DIR / "intraday_5m.parquet"
CANDS_PATH = PILOT_DIR / "candidates.parquet"

PRICE_MIN, PRICE_MAX = 2.0, 20.0
DAILY_GAIN_MIN_PCT = 10.0
RVOL_MIN_PROXY = 2.0

# Fetch 1 month earlier than current pilot start (2026-03-16)
NEW_START = date(2025, 10, 15)
NEW_END = date(2025, 11, 14)


def load_pilot():
    bars = pd.read_parquet(BARS_PATH)
    cands = pd.read_parquet(CANDS_PATH)
    return bars, cands


def get_universe(cands):
    """Use existing pilot ticker universe."""
    return cands["ticker"].unique().tolist()


def fetch_alpaca_daily(symbols, start_date, end_date):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    api_key = os.environ["APCA_API_KEY_ID"]
    api_secret = os.environ["APCA_API_SECRET_KEY"]
    client = StockHistoricalDataClient(api_key, api_secret)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start_date - timedelta(days=30), datetime.min.time()),
        end=datetime.combine(end_date + timedelta(days=1), datetime.min.time()),
        feed="iex",
    )
    try:
        resp = client.get_stock_bars(req)
        return resp.df
    except Exception as e:
        print(f"  daily fetch error: {e}")
        return pd.DataFrame()


def fetch_alpaca_5min(symbols, target_date):
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    api_key = os.environ["APCA_API_KEY_ID"]
    api_secret = os.environ["APCA_API_SECRET_KEY"]
    client = StockHistoricalDataClient(api_key, api_secret)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(5, TimeFrameUnit.Minute),
        start=datetime.combine(target_date, datetime.min.time()),
        end=datetime.combine(target_date + timedelta(days=1), datetime.min.time()),
        feed="iex",
    )
    try:
        resp = client.get_stock_bars(req)
        return resp.df
    except Exception as e:
        print(f"  5min fetch err {target_date}: {e}")
        return pd.DataFrame()


def filter_candidates(daily_df, target_date):
    if daily_df.empty:
        return pd.DataFrame()
    df = daily_df.reset_index()
    if "timestamp" in df.columns:
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    else:
        df["date"] = pd.to_datetime(df.iloc[:, 1]).dt.date
    rows = []
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("date").reset_index(drop=True)
        if target_date not in grp["date"].values:
            continue
        idx = grp.index[grp["date"] == target_date][0]
        if idx == 0:
            continue
        row = grp.iloc[idx]
        prev = grp.iloc[idx - 1]
        prev_close = prev["close"]
        if prev_close <= 0:
            continue
        intraday_pct = (row["close"] - prev_close) / prev_close * 100
        if not (PRICE_MIN <= row["close"] <= PRICE_MAX):
            continue
        if intraday_pct < DAILY_GAIN_MIN_PCT:
            continue
        prior_vol = grp.iloc[max(0, idx-20):idx]["volume"].mean()
        if prior_vol <= 0:
            continue
        rvol = row["volume"] / prior_vol
        if rvol < RVOL_MIN_PROXY:
            continue
        rows.append({
            "ticker": sym,
            "date": pd.Timestamp(target_date),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "daily_pct": float(intraday_pct),
            "intraday_pct": float(intraday_pct),
            "rvol_proxy": float(rvol),
        })
    return pd.DataFrame(rows)


def main():
    bars, cands = load_pilot()
    print(f"Pilot: {len(bars):,} bars, {len(cands):,} candidates")

    universe = get_universe(cands)
    print(f"Universe: {len(universe)} tickers")

    # Build new trading day list (skip weekends)
    new_dates = []
    d = NEW_START
    while d <= NEW_END:
        if d.weekday() < 5:  # Mon-Fri
            new_dates.append(d)
        d += timedelta(days=1)
    print(f"Target trading days: {len(new_dates)} ({new_dates[0]} to {new_dates[-1]})")

    # Fetch daily bars (batched)
    print("\nFetching daily bars...")
    BATCH = 100
    daily_chunks = []
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        df = fetch_alpaca_daily(batch, new_dates[0], new_dates[-1])
        if not df.empty:
            daily_chunks.append(df)
        print(f"  batch {i//BATCH+1}/{(len(universe)//BATCH)+1}: {len(df) if not df.empty else 0} rows")
    if not daily_chunks:
        print("FATAL: no daily data")
        return
    daily_all = pd.concat(daily_chunks)
    print(f"Total daily bars: {len(daily_all):,}")

    # Filter candidates per date
    print("\nFiltering candidates...")
    all_cands = []
    for d in new_dates:
        new_c = filter_candidates(daily_all, d)
        if not new_c.empty:
            all_cands.append(new_c)
            print(f"  {d}: {len(new_c)} candidates")
    if not all_cands:
        print("WARN: no candidates")
        return
    new_cands_df = pd.concat(all_cands, ignore_index=True)
    print(f"Total new candidates: {len(new_cands_df)}")

    # Fetch 5-min bars
    print("\nFetching 5-min bars...")
    new_bars_chunks = []
    for d in new_dates:
        day_cands = new_cands_df[new_cands_df["date"].dt.date == d]
        if day_cands.empty:
            continue
        symbols = day_cands["ticker"].unique().tolist()
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i+50]
            df = fetch_alpaca_5min(batch, d)
            if not df.empty:
                df = df.reset_index()
                df = df.rename(columns={"symbol": "ticker", "timestamp": "datetime"})
                df = df[["datetime", "close", "high", "low", "open", "volume", "ticker"]]
                df["adj close"] = df["close"]
                df = df[["datetime", "adj close", "close", "high", "low", "open", "volume", "ticker"]]
                new_bars_chunks.append(df)
    if not new_bars_chunks:
        print("WARN: no bars")
        return
    new_bars_df = pd.concat(new_bars_chunks, ignore_index=True)
    print(f"New 5-min bars: {len(new_bars_df):,}")

    # Append
    print("\nWriting...")
    combined_bars = pd.concat([bars, new_bars_df], ignore_index=True)
    combined_cands = pd.concat([cands, new_cands_df], ignore_index=True)
    combined_bars.to_parquet(BARS_PATH)
    combined_cands.to_parquet(CANDS_PATH)
    print(f"Bars: {len(bars):,} -> {len(combined_bars):,}")
    print(f"Cands: {len(cands):,} -> {len(combined_cands):,}")


if __name__ == "__main__":
    main()
