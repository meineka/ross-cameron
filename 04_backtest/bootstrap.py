"""
bootstrap.py — Cameron-Strategy free-data pilot pipeline.

Runs end-to-end with $0 cost using yfinance + NASDAQ-Trader-CSV + SEC-EDGAR.
Builds a 60-day Cameron-relevant dataset for backtest.

Pipeline:
  1) Pull all US-listed tickers (NASDAQ Trader public CSV).
  2) For each ticker, fetch ~60 days of daily bars (yfinance batch).
  3) Filter: which (ticker, date) pairs had a "Cameron candidate day"?
       - Open or intraday move ≥ 10% vs prev close
       - Volume ≥ avg(20) × some factor (RVOL proxy)
       - Price between $2 and $20
  4) For each candidate (ticker, date): pull 5-min bars (yfinance, 60-day-window).
  5) Optional: tag with EDGAR 8-K catalyst presence on/near that date.
  6) Save to parquet for downstream backtest.

Dependencies (all free):
  pip install yfinance pandas pyarrow tqdm requests
"""

from __future__ import annotations

import io
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG (mirrors constraints.yaml — keep aligned!)
# ─────────────────────────────────────────────────────────────────────────────
PRICE_MIN = 2.0
PRICE_MAX = 20.0
DAILY_GAIN_MIN_PCT = 10.0           # Pillar 4
RVOL_MIN_PROXY = 2.0                # yfinance Daily-Bars: real RVOL not computable, proxy 2x
DAYS_LOOKBACK_DAILY = 120           # 4 Monate — genug für RVOL(20) + 60d-Filter-Fenster
DAYS_LOOKBACK_INTRADAY = 55         # yfinance 5m-cap is ~60 days, leave margin
SEC_USER_AGENT = "ross-cameron-backtest szymon@example.com"

OUT_DIR = Path(__file__).resolve().parent / "data_pilot"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1) UNIVERSE — alle US-Stocks von NASDAQ Trader (public CSV, no auth)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_us_tickers() -> list[str]:
    """All US-listed tickers from official NASDAQ Trader feed (NASDAQ + NYSE/AMEX)."""
    urls = {
        "nasdaq": "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
        "other": "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",  # NYSE+AMEX
    }
    tickers: set[str] = set()
    for name, url in urls.items():
        log.info("Fetching %s ticker list…", name)
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), sep="|")
        col = "Symbol" if "Symbol" in df.columns else "ACT Symbol"
        # filter test tickers + ETFs (rough)
        df = df[df.get("Test Issue", "N") == "N"]
        if "ETF" in df.columns:
            df = df[df["ETF"] == "N"]
        tickers.update(df[col].dropna().astype(str).tolist())
    tickers = {t for t in tickers if t.isalpha() and 1 <= len(t) <= 5}
    log.info("Universe: %d tickers", len(tickers))
    return sorted(tickers)


# ─────────────────────────────────────────────────────────────────────────────
# 2) DAILY BARS (yfinance batch — fast, free)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_daily_bars(tickers: list[str], days: int = DAYS_LOOKBACK_DAILY,
                     batch_size: int = 100) -> pd.DataFrame:
    """Daily OHLCV across the universe. Returns long-format DataFrame."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    all_dfs: list[pd.DataFrame] = []
    for i in tqdm(range(0, len(tickers), batch_size), desc="Daily-Bars batches"):
        batch = tickers[i : i + batch_size]
        try:
            df = yf.download(
                tickers=batch,
                start=start.isoformat(),
                end=end.isoformat(),
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as e:
            log.warning("batch failed: %s", e)
            continue
        if df.empty:
            continue
        # flatten multiindex
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=0, future_stack=True).rename_axis(["date", "ticker"]).reset_index()
        else:
            df = df.reset_index()
            df["ticker"] = batch[0]
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        all_dfs.append(df)
        time.sleep(0.5)  # be kind to Yahoo
    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3) FILTER — Cameron-Candidate-Days
# ─────────────────────────────────────────────────────────────────────────────
def find_cameron_candidates(daily: pd.DataFrame) -> pd.DataFrame:
    """Returns (ticker, date) pairs that match Cameron's day-level filter."""
    log.info("Filtering Cameron candidates…")
    df = daily.dropna(subset=["close", "open", "volume"]).copy()
    df = df.sort_values(["ticker", "date"])
    df["prev_close"] = df.groupby("ticker")["close"].shift(1)
    df["daily_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100.0
    df["intraday_pct"] = (df["high"] - df["prev_close"]) / df["prev_close"] * 100.0
    df["avg_vol_20"] = df.groupby("ticker")["volume"].transform(
        lambda s: s.rolling(20, min_periods=5).mean()
    )
    df["rvol_proxy"] = df["volume"] / df["avg_vol_20"]
    mask = (
        (df["close"].between(PRICE_MIN, PRICE_MAX))
        & (df["intraday_pct"] >= DAILY_GAIN_MIN_PCT)
        & (df["rvol_proxy"] >= RVOL_MIN_PROXY)
    )
    cands = df.loc[mask, ["ticker", "date", "open", "high", "low", "close",
                          "volume", "daily_pct", "intraday_pct", "rvol_proxy"]]
    log.info("Found %d candidate (ticker, date) pairs", len(cands))
    return cands.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4) INTRADAY (5min) — only for candidate days, last ~55 days reachable
# ─────────────────────────────────────────────────────────────────────────────
def fetch_intraday_for_candidates(cands: pd.DataFrame) -> pd.DataFrame:
    """Pull 5-min bars for candidate (ticker, date) within 55-day window."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=DAYS_LOOKBACK_INTRADAY)
    cands = cands.copy()
    cands["date"] = pd.to_datetime(cands["date"]).dt.date
    cands = cands[cands["date"] >= cutoff]
    log.info("Pulling 5-min bars for %d candidate days within yfinance reach…",
             len(cands))

    out: list[pd.DataFrame] = []
    grouped = cands.groupby("ticker")
    for ticker, grp in tqdm(grouped, desc="5-min pull"):
        dates = sorted(grp["date"].unique())
        start = min(dates) - timedelta(days=2)
        end = max(dates) + timedelta(days=2)
        try:
            df = yf.download(
                ticker,
                start=start.isoformat(),
                end=end.isoformat(),
                interval="5m",
                prepost=True,
                progress=False,
                auto_adjust=False,
            )
        except Exception as e:
            log.warning("5m failed for %s: %s", ticker, e)
            continue
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df["ticker"] = ticker
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        out.append(df)
        time.sleep(0.5)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 5) CATALYST TAGGING — SEC EDGAR 8-K filings on/around candidate date
# ─────────────────────────────────────────────────────────────────────────────
def has_8k_filing(ticker: str, date: pd.Timestamp,
                  window_days: int = 1) -> bool | None:
    """Returns True if ticker filed an 8-K within +/- window_days of date.
    None on lookup failure (don't cache as False)."""
    try:
        # CIK-Lookup (free)
        url = f"https://www.sec.gov/cgi-bin/browse-edgar"
        params = {
            "action": "getcompany",
            "CIK": ticker,
            "type": "8-K",
            "dateb": (pd.Timestamp(date) + pd.Timedelta(days=window_days)).strftime("%Y%m%d"),
            "datea": (pd.Timestamp(date) - pd.Timedelta(days=window_days)).strftime("%Y%m%d"),
            "output": "atom",
        }
        r = requests.get(url, params=params,
                         headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return None
        # rough check: look for at least one <entry> tag in atom feed
        return "<entry>" in r.text
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    # 1) Universe
    tickers_path = OUT_DIR / "universe.parquet"
    if tickers_path.exists():
        tickers = pd.read_parquet(tickers_path)["ticker"].tolist()
        log.info("Universe loaded from cache: %d", len(tickers))
    else:
        tickers = fetch_us_tickers()
        pd.DataFrame({"ticker": tickers}).to_parquet(tickers_path)

    # 2) Daily-Bars
    daily_path = OUT_DIR / "daily.parquet"
    if daily_path.exists():
        daily = pd.read_parquet(daily_path)
        log.info("Daily loaded from cache: %d rows", len(daily))
    else:
        daily = fetch_daily_bars(tickers)
        if daily.empty:
            log.error("No daily data fetched — aborting")
            return
        daily.to_parquet(daily_path)

    # 3) Cameron-Candidate-Filter
    cands = find_cameron_candidates(daily)
    cands.to_parquet(OUT_DIR / "candidates.parquet")
    log.info("Top 10 candidate days by intraday-move:\n%s",
             cands.nlargest(10, "intraday_pct").to_string())

    # 4) 5-min für reachable candidate days
    intraday = fetch_intraday_for_candidates(cands)
    if not intraday.empty:
        intraday.to_parquet(OUT_DIR / "intraday_5m.parquet")
        log.info("Saved %d 5-min rows across %d ticker-days",
                 len(intraday), intraday["ticker"].nunique())

    # 5) Optional EDGAR-Tagging (slow, ratelimited — sample first 50 candidates)
    sample = cands.head(50).copy()
    log.info("Tagging EDGAR 8-K presence (sample of %d candidates)…", len(sample))
    sample["has_8k"] = [
        has_8k_filing(r.ticker, r.date) for r in tqdm(sample.itertuples(),
                                                     total=len(sample))
    ]
    sample.to_parquet(OUT_DIR / "candidates_with_catalyst_sample.parquet")
    log.info("EDGAR-Sample-Match-Rate: %.1f %%",
             100.0 * sample["has_8k"].fillna(False).mean())

    log.info("DONE. Outputs in %s", OUT_DIR)


if __name__ == "__main__":
    main()
