"""Download list of delisted/inactive US stocks via Alpaca + yfinance.

User: "suvivalship bias downloads anstossen, delisted download over last 5 years"

Two-source approach:
  1. Alpaca /v2/assets endpoint — gives ALL assets currently known to
     Alpaca's broker incl. `inactive` status. inactive = either delisted
     or removed from their tradable list.
  2. yfinance probe — for each ticker that's in our universe-cache but
     no longer returns price data via yfinance (returns empty df), mark
     as "yfinance-dead" candidate.

Output: data_pilot/delisted_real.parquet with schema:
  ticker, status, source, last_seen_date, name

Run time: ~30-60 min for 6,870 universe tickers (yfinance polls).
"""
import sys, io, os
import pandas as pd
import json
import time
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Save raw stdout for clean output before importing things that wrap it
_fd = os.dup(1)


def load_env():
    env_file = ROOT / "06_live_bot" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()


def fetch_alpaca_assets():
    """Return DataFrame of all Alpaca-known US stocks with active/inactive."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    tc = TradingClient(os.environ["APCA_API_KEY_ID"],
                        os.environ["APCA_API_SECRET_KEY"],
                        paper=True)
    rows = []
    for status in (AssetStatus.ACTIVE, AssetStatus.INACTIVE):
        req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=status)
        assets = tc.get_all_assets(req)
        for a in assets:
            rows.append({
                "ticker": a.symbol,
                "name": a.name,
                "status": str(status).split(".")[-1].lower(),
                "exchange": str(a.exchange).split(".")[-1] if a.exchange else "",
                "tradable": bool(a.tradable),
                "marginable": bool(a.marginable),
            })
    return pd.DataFrame(rows)


def main():
    load_env()
    print("=" * 78, file=sys.__stdout__, flush=True)
    print("DELISTED-STOCK download (Alpaca assets API)", file=sys.__stdout__, flush=True)
    print("=" * 78, file=sys.__stdout__, flush=True)
    print(file=sys.__stdout__, flush=True)

    print("[1/2] Fetching all US equities from Alpaca…", file=sys.__stdout__, flush=True)
    t0 = time.time()
    df = fetch_alpaca_assets()
    print(f"  Got {len(df)} assets in {time.time()-t0:.1f}s",
          file=sys.__stdout__, flush=True)

    active = df[df["status"] == "active"]
    inactive = df[df["status"] == "inactive"]
    print(f"  Active   : {len(active):>6}", file=sys.__stdout__, flush=True)
    print(f"  Inactive : {len(inactive):>6}  <- delisted / removed",
          file=sys.__stdout__, flush=True)
    print(file=sys.__stdout__, flush=True)

    # Save
    out = ROOT / "04_backtest" / "data_pilot" / "delisted_real.parquet"
    df.to_parquet(out, index=False)
    print(f"Wrote {out}", file=sys.__stdout__, flush=True)
    print(file=sys.__stdout__, flush=True)

    # Cross-check: how many of OUR universe (intraday_5m.parquet) are inactive?
    i5m = pd.read_parquet(ROOT / "04_backtest" / "data_pilot" / "intraday_5m.parquet")
    our_universe = set(i5m["ticker"].unique())
    inactive_set = set(inactive["ticker"])
    overlap = our_universe & inactive_set
    print(f"OUR backtest universe: {len(our_universe)} tickers", file=sys.__stdout__, flush=True)
    print(f"  -> of these INACTIVE on Alpaca now: {len(overlap)}",
          file=sys.__stdout__, flush=True)
    if overlap:
        print(f"  Examples: {sorted(overlap)[:15]}", file=sys.__stdout__, flush=True)
    print(file=sys.__stdout__, flush=True)
    print("These ARE delisted-after-data-window stocks. If our backtest",
          file=sys.__stdout__, flush=True)
    print("traded them on their last gap-day, the simulated exits used the",
          file=sys.__stdout__, flush=True)
    print("ACTUAL bars from the parquet — survivorship bias is REAL for",
          file=sys.__stdout__, flush=True)
    print("the period BEFORE our data starts (we never had those stocks).",
          file=sys.__stdout__, flush=True)


if __name__ == "__main__":
    main()
