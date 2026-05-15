"""historical_data_loader.py — Phase-52 (2026-05-15)

Extends 04_backtest/data_pilot/intraday_5m.parquet with N more
trading days of 5-min OHLCV bars from Alpaca Historical (IEX feed,
free with paper account).

User-requested in Cameron-rules audit follow-up: more pilot days =
more statistical power for backtest sweeps.

Usage:
    python historical_data_loader.py --days 100 --top-n 30
    python historical_data_loader.py --days 50 --symbols-from-watchlist
    python historical_data_loader.py --days 30 --extend-existing

Strategy:
  1. Select symbol universe (default: TradingView top-N gappers today;
     `--extend-existing` uses unique symbols already in pilot parquet).
  2. For each of the last `--days` trading days:
       - Fetch Alpaca 5m bars 07:00-12:00 ET for each symbol
       - Skip if already in parquet (idempotent re-run safe)
       - Append to a NEW parquet `intraday_5m_ext.parquet`
  3. At end, merge with existing parquet (dedup on datetime+ticker).

Cost: max ~symbols × days Alpaca REST calls. With 200/min rate cap
that's well under 15 min for 30×100. Free tier (IEX feed).
"""
from __future__ import annotations
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_DIR = ROOT / "04_backtest" / "data_pilot"
EXT_PARQUET = PILOT_DIR / "intraday_5m_ext.parquet"

sys.path.insert(0, str(HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("hist-loader")


def trading_days_back(n: int, anchor: datetime | None = None) -> list[datetime]:
    """Last n weekdays counting back from `anchor` (default = today UTC)."""
    if anchor is None:
        anchor = datetime.now(timezone.utc)
    days = []
    d = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
    while len(days) < n:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d = d - timedelta(days=1)
    return sorted(days)


def get_top_gappers_today(top_n: int = 30) -> list[str]:
    """Pull current top-N premarket gainers via TradingView (no auth)."""
    try:
        from scanners.tradingview_scanner import scan_cameron_candidates
        rows = scan_cameron_candidates(top_n=top_n)
        return [r["ticker"] for r in rows if r.get("ticker")]
    except Exception as e:
        log.warning("TV scanner failed: %s — falling back to empty list", e)
        return []


def load_existing_symbols() -> list[str]:
    """Read unique tickers from existing pilot parquet."""
    import pandas as pd
    main_p = PILOT_DIR / "intraday_5m.parquet"
    if not main_p.exists():
        return []
    df = pd.read_parquet(main_p, columns=["ticker"])
    return sorted(df["ticker"].unique().tolist())


def fetch_alpaca_bars(symbol: str, day: datetime,
                        client) -> list[dict] | None:
    """Pull Alpaca 5m bars for one symbol, one day, 07:00-12:00 ET."""
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        # ET window 07:00-12:00 = UTC 11:00-16:00 (DST: 12:00-17:00 in winter).
        # Use 11:00-17:00 UTC to span both.
        start = day.replace(hour=11, minute=0, second=0, microsecond=0,
                             tzinfo=timezone.utc)
        end = day.replace(hour=17, minute=0, second=0, microsecond=0,
                           tzinfo=timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=100,
                    help="how many trading days back to fetch (default 100)")
    ap.add_argument("--top-n", type=int, default=30,
                    help="how many top symbols (default 30)")
    ap.add_argument("--symbols-from-watchlist", action="store_true",
                    help="use current TradingView top-N as universe (default)")
    ap.add_argument("--extend-existing", action="store_true",
                    help="use existing pilot ticker universe instead")
    ap.add_argument("--alpaca-rate-per-min", type=int, default=180,
                    help="self-impose this REST rate cap (default 180)")
    args = ap.parse_args()

    # Init Alpaca client (Phase-57: guarded)
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

    # Select symbol universe
    if args.extend_existing:
        symbols = load_existing_symbols()
        log.info("Universe: %d symbols from existing pilot", len(symbols))
        # Limit to top-N to keep runtime bounded
        if len(symbols) > args.top_n:
            symbols = symbols[: args.top_n]
            log.info("Truncated to top-%d alphabetical", args.top_n)
    else:
        symbols = get_top_gappers_today(top_n=args.top_n)
        log.info("Universe: %d symbols from TradingView top-gappers today",
                  len(symbols))
    if not symbols:
        log.error("Universe empty — aborting")
        return 1
    log.info("Symbols: %s", symbols)

    # Compute trading days
    days = trading_days_back(args.days)
    log.info("Days: %d trading days from %s to %s",
              len(days), days[0].date(), days[-1].date())

    # Read existing parquet to skip already-covered (symbol, day) pairs
    import pandas as pd
    existing: set[tuple[str, str]] = set()
    main_p = PILOT_DIR / "intraday_5m.parquet"
    if main_p.exists():
        try:
            df_main = pd.read_parquet(main_p, columns=["datetime", "ticker"])
            df_main["date"] = pd.to_datetime(df_main["datetime"]).dt.date
            existing = set(zip(df_main["ticker"], df_main["date"].astype(str)))
            log.info("Main parquet: %d existing (symbol, day) pairs",
                      len(existing))
        except Exception as e:
            log.warning("main parquet read failed: %s", e)
    if EXT_PARQUET.exists():
        try:
            df_ext = pd.read_parquet(EXT_PARQUET, columns=["datetime", "ticker"])
            df_ext["date"] = pd.to_datetime(df_ext["datetime"]).dt.date
            for t, d in zip(df_ext["ticker"], df_ext["date"].astype(str)):
                existing.add((t, d))
            log.info("After ext-parquet merge: %d pairs",
                      len(existing))
        except Exception:
            pass

    # Rate-limiting: spread calls so we stay under args.alpaca_rate_per_min
    sleep_per_call = 60.0 / max(1, args.alpaca_rate_per_min)
    log.info("Rate cap: %d/min → sleep %.2fs between calls",
              args.alpaca_rate_per_min, sleep_per_call)

    new_rows: list[dict] = []
    n_fetched = n_skipped = n_empty = n_total = 0
    t0 = time.monotonic()
    for day in days:
        date_str = str(day.date())
        for sym in symbols:
            n_total += 1
            if (sym, date_str) in existing:
                n_skipped += 1
                continue
            rows = fetch_alpaca_bars(sym, day, data_client)
            if rows is None:
                n_empty += 1
            else:
                new_rows.extend(rows)
                n_fetched += 1
            time.sleep(sleep_per_call)
            if n_total % 50 == 0:
                elapsed = time.monotonic() - t0
                log.info("Progress: %d/%d (fetched=%d, empty=%d, skip=%d, "
                         "elapsed=%.0fs)",
                         n_total, len(days) * len(symbols),
                         n_fetched, n_empty, n_skipped, elapsed)

    log.info("Done. fetched=%d, empty=%d, already-present=%d, rows=%d",
              n_fetched, n_empty, n_skipped, len(new_rows))

    if not new_rows:
        log.info("No new rows — nothing to write")
        return 0

    df_new = pd.DataFrame(new_rows)
    df_new["datetime"] = pd.to_datetime(df_new["datetime"], utc=True)
    if EXT_PARQUET.exists():
        df_old = pd.read_parquet(EXT_PARQUET)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
        df_combined = df_combined.drop_duplicates(
            subset=["datetime", "ticker"], keep="last")
    else:
        df_combined = df_new
    df_combined.to_parquet(EXT_PARQUET, index=False)
    log.info("Wrote %d rows to %s (total: %d rows)",
              len(df_new), EXT_PARQUET, len(df_combined))
    return 0


if __name__ == "__main__":
    sys.exit(main())
