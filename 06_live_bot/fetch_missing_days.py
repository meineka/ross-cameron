"""Fetch missing pilot days via Alpaca historical API.
Target: 2026-05-09 to 2026-05-13 (4 trading days).

Strategy:
1. Use existing ticker-universe from previous pilot days
2. Filter: tickers that had a "Cameron candidate day" in last 30 days
3. Fetch daily bars to find new candidate-days
4. For top candidates: fetch 5-min bars
5. Append to existing parquets

This is incremental — won't refetch existing dates.
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

# Existing pilot data paths
PILOT_DIR = Path(__file__).resolve().parents[1] / "04_backtest" / "data_pilot"
BARS_PATH = PILOT_DIR / "intraday_5m.parquet"
CANDS_PATH = PILOT_DIR / "candidates.parquet"

# Cameron rules (mirror bootstrap.py)
PRICE_MIN, PRICE_MAX = 2.0, 20.0
DAILY_GAIN_MIN_PCT = 10.0
RVOL_MIN_PROXY = 2.0

# Fetch range
NEW_DATES = [date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 13)]


def load_pilot():
    bars = pd.read_parquet(BARS_PATH)
    cands = pd.read_parquet(CANDS_PATH)
    return bars, cands


def get_recurring_tickers(cands, min_appearances=3):
    """Tickers that appeared as candidate >= N times in pilot."""
    counts = cands["ticker"].value_counts()
    recurring = counts[counts >= min_appearances].index.tolist()
    return recurring


def fetch_alpaca_daily(symbols, start_date, end_date):
    """Daily bars for symbols via Alpaca."""
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
    """5-min bars for single trading day via Alpaca."""
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


def filter_candidates_for_date(daily_df, target_date):
    """From daily-bars df, find tickers with Cameron-candidate-pattern on target_date."""
    if daily_df.empty:
        return pd.DataFrame()
    df = daily_df.reset_index()
    if "timestamp" in df.columns:
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    else:
        df["date"] = pd.to_datetime(df.iloc[:, 1]).dt.date
    # Need prev close → group by symbol, shift
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
        # Need price-range
        if not (PRICE_MIN <= row["close"] <= PRICE_MAX):
            continue
        if intraday_pct < DAILY_GAIN_MIN_PCT:
            continue
        # rvol proxy = volume / avg(20)
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

    # Get recurring tickers (appeared >= 3x in pilot)
    recurring = get_recurring_tickers(cands, min_appearances=3)
    print(f"Recurring tickers (>=3 appearances): {len(recurring)}")

    # Also include the live-known HSPT and recent activity tickers
    extra = ["HSPT", "MNTS", "WKHS", "VIVO", "TRT", "LONA", "KSCP", "ICU",
             "EDSA", "SKYQ", "IPWR", "ANNA", "FGI", "MSC"]
    universe = sorted(set(recurring) | set(extra))
    print(f"Total universe to fetch: {len(universe)}")

    # Fetch daily bars in batches of 100
    print("\nFetching daily bars (Alpaca)…")
    BATCH = 100
    daily_chunks = []
    for i in range(0, len(universe), BATCH):
        batch = universe[i:i+BATCH]
        df = fetch_alpaca_daily(batch, NEW_DATES[0], NEW_DATES[-1])
        if not df.empty:
            daily_chunks.append(df)
        print(f"  batch {i//BATCH+1}: fetched {len(df) if not df.empty else 0} rows")
    if not daily_chunks:
        print("FATAL: no daily data — abort")
        return
    daily_all = pd.concat(daily_chunks)
    print(f"\nTotal daily bars: {len(daily_all):,}")

    # Filter candidates per new date
    print("\nFiltering candidates per date…")
    all_new_cands = []
    for d in NEW_DATES:
        new_cands = filter_candidates_for_date(daily_all, d)
        print(f"  {d}: {len(new_cands)} candidates")
        all_new_cands.append(new_cands)

    new_cands_df = pd.concat(all_new_cands, ignore_index=True)
    if new_cands_df.empty:
        print("WARN: no new candidates passed filter — abort")
        return
    print(f"\nTotal new candidates: {len(new_cands_df)}")
    print(new_cands_df[["ticker", "date", "close", "intraday_pct", "rvol_proxy"]].head(20).to_string())

    # Fetch 5-min bars for candidate-tickers per their date
    print("\nFetching 5-min bars for candidates…")
    new_bars_chunks = []
    for d in NEW_DATES:
        day_cands = new_cands_df[new_cands_df["date"].dt.date == d]
        if day_cands.empty:
            continue
        symbols = day_cands["ticker"].unique().tolist()
        # Fetch in batches
        for i in range(0, len(symbols), 50):
            batch = symbols[i:i+50]
            df = fetch_alpaca_5min(batch, d)
            if not df.empty:
                df = df.reset_index()
                df = df.rename(columns={"symbol": "ticker", "timestamp": "datetime"})
                # Keep only needed cols matching pilot schema
                df = df[["datetime", "close", "high", "low", "open", "volume", "ticker"]]
                df["adj close"] = df["close"]
                df = df[["datetime", "adj close", "close", "high", "low", "open", "volume", "ticker"]]
                new_bars_chunks.append(df)
            print(f"  {d} batch {i//50+1}: {len(df) if not df.empty else 0} bars")

    if not new_bars_chunks:
        print("WARN: no 5-min bars fetched — abort")
        return
    new_bars_df = pd.concat(new_bars_chunks, ignore_index=True)
    print(f"\nNew 5-min bars: {len(new_bars_df):,}")

    # Append to existing parquets
    print("\nWriting updated parquets…")
    combined_bars = pd.concat([bars, new_bars_df], ignore_index=True)
    combined_cands = pd.concat([cands, new_cands_df], ignore_index=True)

    # Backup originals
    BARS_PATH.with_suffix(".parquet.bak").write_bytes(BARS_PATH.read_bytes())
    CANDS_PATH.with_suffix(".parquet.bak").write_bytes(CANDS_PATH.read_bytes())

    combined_bars.to_parquet(BARS_PATH)
    combined_cands.to_parquet(CANDS_PATH)

    print(f"\nDone. Bars: {len(bars):,} → {len(combined_bars):,}")
    print(f"Candidates: {len(cands):,} → {len(combined_cands):,}")
    print(f"Backups: {BARS_PATH.with_suffix('.parquet.bak')} & {CANDS_PATH.with_suffix('.parquet.bak')}")


if __name__ == "__main__":
    main()
