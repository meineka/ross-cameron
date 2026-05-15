"""fetch_historical_range.py — Phase-62 (2026-05-15)

Companion to historical_data_loader.py but with explicit start/end
date range instead of "last N trading days back from today". Built
to fill historical gaps in pilot data.

Supports BOTH 1m and 5m timeframes via --timeframe flag. Output
parquet name reflects the chosen timeframe so 1m and 5m datasets
co-exist without overwriting each other.

Usage:
    # Fetch 2025-Jan to 2025-Sep at 1-MINUTE bars (Phase-62 user ask)
    python fetch_historical_range.py --start 2025-01-02 --end 2025-09-14 \\
        --top-n 50 --timeframe 1m

    # Same range at 5m (existing default)
    python fetch_historical_range.py --start 2025-01-02 --end 2025-09-14 \\
        --top-n 50 --timeframe 5m

    # Specific symbols only
    python fetch_historical_range.py --start 2025-01-02 --end 2025-03-31 \\
        --symbols AAPL,TSLA --timeframe 1m

Strategy mirrors historical_data_loader.py — same Alpaca-IEX free feed,
same self-imposed rate cap, idempotent skip-existing-pairs logic.
Output: intraday_1m_ext.parquet OR intraday_5m_ext.parquet depending
on --timeframe.
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_DIR = ROOT / "04_backtest" / "data_pilot"
# EXT_PARQUET resolved at runtime based on --timeframe
DEFAULT_TIMEFRAME = "1m"

sys.path.insert(0, str(HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("hist-range")


def trading_days_in_range(start: datetime, end: datetime) -> list[datetime]:
    """All Mon-Fri weekdays in [start, end] inclusive. Holiday-naive
    but the bar fetcher returns empty on holidays anyway, so it's a
    no-op cost rather than a correctness issue."""
    days = []
    d = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end_d = end.replace(hour=0, minute=0, second=0, microsecond=0)
    while d <= end_d:
        if d.weekday() < 5:
            days.append(d)
        d = d + timedelta(days=1)
    return days


def fetch_alpaca_bars(symbol: str, day: datetime, client,
                         *, tf_minutes: int = 1) -> list[dict] | None:
    """Same shape as historical_data_loader.fetch_alpaca_bars but
    duplicated here to avoid coupling. 07:00-12:00 ET window, IEX feed.

    tf_minutes: 1 or 5 — bar resolution. 1m gives 5× more rows per
    call and is what Cameron's live scanner uses for entry triggers."""
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        start = day.replace(hour=11, minute=0, second=0, microsecond=0,
                             tzinfo=timezone.utc)
        end = day.replace(hour=17, minute=0, second=0, microsecond=0,
                           tzinfo=timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame(tf_minutes, TimeFrameUnit.Minute),
            start=start, end=end, feed="iex",
        )
        bars = client.get_stock_bars(req)
        rows = bars.data.get(symbol, []) if hasattr(bars, "data") else []
        if not rows:
            return None
        return [
            {
                "datetime": b.timestamp,
                "open": float(b.open), "high": float(b.high),
                "low": float(b.low), "close": float(b.close),
                "adj close": float(b.close),
                "volume": float(b.volume), "ticker": symbol,
            } for b in rows
        ]
    except Exception as e:
        log.debug("alpaca bars %s %s: %s", symbol, day.date(), e)
        return None


def load_existing_symbols(limit: int) -> list[str]:
    """Read top-N most-frequent tickers from existing 5m pilot — same
    universe applies for 1m backfill since these are the Cameron-relevant
    float<10M premarket-movers either way."""
    import pandas as pd
    main_p = PILOT_DIR / "intraday_5m.parquet"
    if not main_p.exists():
        return []
    df = pd.read_parquet(main_p, columns=["ticker"])
    counts = df["ticker"].value_counts()
    return counts.head(limit).index.tolist()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True,
                     help="YYYY-MM-DD inclusive (e.g. 2025-01-02)")
    ap.add_argument("--end", required=True,
                     help="YYYY-MM-DD inclusive (e.g. 2025-09-14)")
    ap.add_argument("--timeframe", choices=["1m", "5m"],
                     default=DEFAULT_TIMEFRAME,
                     help="bar resolution (default 1m — matches live "
                          "scanner). Output filename reflects this.")
    ap.add_argument("--top-n", type=int, default=50,
                     help="symbols to backfill (default 50 most-frequent "
                          "from existing pilot)")
    ap.add_argument("--symbols", type=str, default=None,
                     help="comma-separated explicit ticker list, "
                          "overrides --top-n")
    ap.add_argument("--alpaca-rate-per-min", type=int, default=180,
                     help="self-impose this REST rate cap (default 180)")
    args = ap.parse_args()

    tf_minutes = 1 if args.timeframe == "1m" else 5
    ext_parquet = PILOT_DIR / f"intraday_{args.timeframe}_ext.parquet"
    main_parquet = PILOT_DIR / f"intraday_{args.timeframe}.parquet"
    log.info("Timeframe: %s (%d-minute bars) → %s",
              args.timeframe, tf_minutes, ext_parquet.name)

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    if end_dt < start_dt:
        log.error("end < start: %s < %s", end_dt, start_dt)
        return 1

    # Init Alpaca client (use guarded if available, raw otherwise — backfill
    # is a tool, not part of the live REST budget)
    try:
        from secrets_loader import get_alpaca_keys
        try:
            from guarded_alpaca import GuardedStockHistoricalDataClient as _DC
        except Exception:
            from alpaca.data.historical import StockHistoricalDataClient as _DC
        k, s = get_alpaca_keys()
        data_client = _DC(k, s)
    except Exception as e:
        log.error("Alpaca init failed: %s", e)
        return 1

    # Symbol universe
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")
                    if s.strip()]
        log.info("Universe: %d symbols from --symbols", len(symbols))
    else:
        symbols = load_existing_symbols(args.top_n)
        log.info("Universe: %d top-frequent symbols from existing pilot",
                  len(symbols))
    if not symbols:
        log.error("Universe empty — pass --symbols or ensure pilot exists")
        return 1
    log.info("Symbols (first 10): %s%s",
              symbols[:10], " ..." if len(symbols) > 10 else "")

    days = trading_days_in_range(start_dt, end_dt)
    log.info("Range: %d trading days from %s to %s",
              len(days), days[0].date(), days[-1].date())

    # Skip already-covered (symbol, day) pairs — only check the SAME
    # timeframe; 5m coverage doesn't dedupe 1m fetches and vice versa.
    import pandas as pd
    existing: set[tuple[str, str]] = set()
    if main_parquet.exists():
        try:
            df_main = pd.read_parquet(main_parquet,
                                        columns=["datetime", "ticker"])
            df_main["date"] = pd.to_datetime(df_main["datetime"]).dt.date
            existing = set(zip(df_main["ticker"], df_main["date"].astype(str)))
            log.info("Main %s parquet: %d existing (symbol, day) pairs",
                      args.timeframe, len(existing))
        except Exception as e:
            log.warning("main parquet read failed: %s", e)
    if ext_parquet.exists():
        try:
            df_ext = pd.read_parquet(ext_parquet,
                                       columns=["datetime", "ticker"])
            df_ext["date"] = pd.to_datetime(df_ext["datetime"]).dt.date
            for t, d in zip(df_ext["ticker"], df_ext["date"].astype(str)):
                existing.add((t, d))
            log.info("After %s ext-parquet merge: %d pairs",
                      args.timeframe, len(existing))
        except Exception:
            pass

    # Fetch loop with self-imposed rate cap
    rate_cap = args.alpaca_rate_per_min
    min_interval_s = 60.0 / rate_cap
    new_rows: list[dict] = []
    n_calls = 0
    n_skipped = 0
    n_empty = 0
    t_run_start = time.monotonic()
    for sym in symbols:
        for day in days:
            key = (sym, str(day.date()))
            if key in existing:
                n_skipped += 1
                continue
            t_call = time.monotonic()
            rows = fetch_alpaca_bars(sym, day, data_client,
                                       tf_minutes=tf_minutes)
            n_calls += 1
            if rows:
                new_rows.extend(rows)
            else:
                n_empty += 1
            # Pace
            elapsed = time.monotonic() - t_call
            if elapsed < min_interval_s:
                time.sleep(min_interval_s - elapsed)
            # Progress every 100 calls
            if n_calls % 100 == 0:
                rate = n_calls / max(time.monotonic() - t_run_start, 1e-6)
                log.info("Progress: calls=%d new_rows=%d empty=%d "
                          "skipped=%d rate=%.1f/s",
                          n_calls, len(new_rows), n_empty, n_skipped, rate)

    elapsed_total = time.monotonic() - t_run_start
    log.info("DONE: calls=%d new_rows=%d empty=%d skipped=%d in %.1fs",
              n_calls, len(new_rows), n_empty, n_skipped, elapsed_total)

    if not new_rows:
        log.info("No new bars — nothing to write")
        return 0

    # Append to timeframe-specific ext parquet
    df_new = pd.DataFrame(new_rows)
    if ext_parquet.exists():
        df_old = pd.read_parquet(ext_parquet)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
        df_combined = df_combined.drop_duplicates(
            subset=["datetime", "ticker"], keep="last"
        )
    else:
        df_combined = df_new
    df_combined.to_parquet(ext_parquet, index=False)
    log.info("Wrote %d rows total to %s", len(df_combined), ext_parquet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
