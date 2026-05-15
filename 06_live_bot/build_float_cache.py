"""build_float_cache.py — Phase-63 (2026-05-16)

Build a persistent per-ticker float-shares cache for Cameron-strict
backtest filtering (FLOAT_MAX_SHARES = 10_000_000).

Why a cache, not a per-call lookup:
  - 5000+ Cameron-universe symbols × yfinance.info latency would take
    ~3-4 hours every backtest run.
  - Float changes rarely (offerings, splits, lock-up expiries) —
    weekly refresh is plenty.
  - Finviz is the de-facto float source for momentum day-traders, and
    covers small-caps + recent IPOs that yfinance often misses.

Data sources (in lookup order):
  1. Finviz scrape — https://finviz.com/quote.ashx?t=<ticker>
                     Float string in the snapshot table ("Shs Float")
                     Respectful: 1.5s delay, real-browser User-Agent
  2. yfinance fallback — yf.Ticker(t).get_info()["floatShares"]
                          Used when Finviz returns 404 / no quote

Cache schema (float_cache.json):
  {
    "AAPL": {
      "float_shares": 15234567890,
      "source": "finviz" | "yfinance" | "none",
      "fetched_at": "2026-05-16T00:00:00Z",
      "error": null | "<error str>"
    },
    ...
  }

CLI:
  python build_float_cache.py                   # full refresh (cache > 7d)
  python build_float_cache.py --tickers AAPL,TSLA  # just these
  python build_float_cache.py --limit 50           # smoke test
  python build_float_cache.py --max-age 30         # only re-fetch >30d old
  python build_float_cache.py --no-yfinance        # Finviz-only (fast)
"""
from __future__ import annotations
import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE_PATH = HERE / "float_cache.json"
CANDIDATES_PARQUET = ROOT / "04_backtest" / "data_pilot" / "candidates.parquet"

sys.path.insert(0, str(HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("float-cache")

# Pretend to be a real browser — Finviz blocks default Python UA
FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

FINVIZ_URL_TEMPLATE = "https://finviz.com/quote.ashx?t={ticker}"
FINVIZ_DELAY_SEC = 1.5  # be respectful; Finviz tolerates <1 req/sec well


def _parse_float_str(s: str | None) -> int | None:
    """Convert Finviz/yfinance float string like '12.34M', '1.23B',
    '500K', '-', '' → int shares. Returns None on unparseable input.

    Examples:
      '12.34M' → 12_340_000
      '1.23B' → 1_230_000_000
      '500K' → 500_000
      '-' or '' or None → None
    """
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if not s or s == "-":
        return None
    # Strip trailing % (some Finviz fields have it)
    if s.endswith("%"):
        return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*([KMB])?$", s, re.IGNORECASE)
    if not m:
        # Plain number without suffix
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return None
    num = float(m.group(1))
    suffix = (m.group(2) or "").upper()
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(
        suffix, 1
    )
    val = int(round(num * mult))
    return val if val > 0 else None


def _extract_finviz_float(html: str) -> int | None:
    """Parse Finviz quote page HTML, return float-shares as int or None.

    Finviz snapshot-table structure (as of 2026): rows of
    <td class="snapshot-td2-cp">label</td><td class="snapshot-td2">value</td>
    pairs. We look for the row labeled exactly 'Shs Float'.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # Snapshot table: find all td.snapshot-td2 cells alongside td.snapshot-td2-cp
    cells = soup.select("td.snapshot-td2-cp, td.snapshot-td2")
    # Walk in pairs: label then value
    for i in range(0, len(cells) - 1, 2):
        label = cells[i].get_text(strip=True)
        if label == "Shs Float":
            value = cells[i + 1].get_text(strip=True)
            return _parse_float_str(value)
    # Fallback: regex over raw HTML for "Shs Float" near a number
    m = re.search(
        r"Shs Float</td>\s*<td[^>]*>\s*<b>([^<]+)</b>", html
    )
    if m:
        return _parse_float_str(m.group(1))
    m = re.search(r"Shs Float[^0-9-]+([\d.]+[KMB]?|-)", html)
    if m:
        return _parse_float_str(m.group(1))
    return None


def _finviz_lookup(ticker: str, session) -> dict:
    """Fetch one ticker from Finviz. Returns dict with float_shares,
    error, http_status. Never raises."""
    url = FINVIZ_URL_TEMPLATE.format(ticker=ticker)
    try:
        r = session.get(url, headers=FINVIZ_HEADERS, timeout=15)
        if r.status_code == 404:
            return {"float_shares": None, "error": "finviz_404",
                    "http_status": 404}
        if r.status_code == 429:
            return {"float_shares": None, "error": "finviz_429_rate_limited",
                    "http_status": 429}
        if r.status_code != 200:
            return {"float_shares": None,
                    "error": f"finviz_http_{r.status_code}",
                    "http_status": r.status_code}
        # Finviz sometimes returns 200 with "Quote not found" body
        body = r.text
        if "Quote not found" in body or "No matching ticker" in body:
            return {"float_shares": None, "error": "finviz_no_quote",
                    "http_status": 200}
        flt = _extract_finviz_float(body)
        if flt is None:
            return {"float_shares": None,
                    "error": "finviz_float_field_missing",
                    "http_status": 200}
        return {"float_shares": flt, "error": None, "http_status": 200}
    except Exception as e:
        return {"float_shares": None,
                "error": f"finviz_exception:{type(e).__name__}:{str(e)[:100]}",
                "http_status": None}


def _yfinance_lookup(ticker: str) -> dict:
    """Fallback: yfinance.Ticker(t).get_info()['floatShares']. Slower
    than Finviz, but covers some edge cases (foreign listings)."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # Prefer fast_info (lighter call) when it has float, else full info
        flt = None
        try:
            fi = t.fast_info
            flt = getattr(fi, "shares", None)
        except Exception:
            pass
        if not flt:
            info = t.get_info() if hasattr(t, "get_info") else t.info
            flt = info.get("floatShares") if info else None
            if not flt:
                flt = info.get("sharesOutstanding") if info else None
        if flt and int(flt) > 0:
            return {"float_shares": int(flt), "error": None}
        return {"float_shares": None, "error": "yfinance_no_float"}
    except Exception as e:
        return {"float_shares": None,
                "error": f"yfinance_exception:{type(e).__name__}:{str(e)[:100]}"}


def lookup_one(ticker: str, *, session=None,
                use_yfinance_fallback: bool = True) -> dict:
    """Primary Finviz, optional yfinance fallback. Returns the
    canonical cache record (dict) for one ticker — never raises."""
    import requests
    if session is None:
        session = requests.Session()
    fin = _finviz_lookup(ticker, session)
    if fin["float_shares"] is not None:
        return {
            "float_shares": fin["float_shares"],
            "source": "finviz",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
    # Finviz failed — try yfinance unless disabled
    if use_yfinance_fallback:
        yf_res = _yfinance_lookup(ticker)
        if yf_res["float_shares"] is not None:
            return {
                "float_shares": yf_res["float_shares"],
                "source": "yfinance",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }
        combined_err = f"{fin['error']}|yfinance:{yf_res['error']}"
    else:
        combined_err = fin["error"]
    return {
        "float_shares": None,
        "source": "none",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": combined_err,
    }


def load_cache(path: Path = CACHE_PATH) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("cache parse failed (%s) — starting fresh", e)
        return {}


def save_cache(cache: dict[str, dict], path: Path = CACHE_PATH) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True),
                    encoding="utf-8")
    tmp.replace(path)


def _is_stale(record: dict, max_age_days: int) -> bool:
    """A cache record needs re-fetch if it's older than max_age_days OR
    if it has a transient-looking error (rate-limit, exception) that
    might succeed on retry."""
    if not record:
        return True
    fetched_at = record.get("fetched_at")
    if not fetched_at:
        return True
    try:
        ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - ts
        if age > timedelta(days=max_age_days):
            return True
    except Exception:
        return True
    # Retry transient errors regardless of age
    err = record.get("error")
    if err and any(t in err for t in ("429", "rate_limited", "exception")):
        return True
    return False


def load_universe_tickers(parquet_path: Path = CANDIDATES_PARQUET
                            ) -> list[str]:
    """Default universe: unique tickers from candidates.parquet, which
    already encodes the price+gap+RVOL filter. Subset of the float-cache
    that actually matters for backtest."""
    try:
        import pandas as pd
        df = pd.read_parquet(parquet_path, columns=["ticker"])
        return sorted(df["ticker"].dropna().astype(str).unique().tolist())
    except Exception as e:
        log.error("could not load tickers from %s: %s", parquet_path, e)
        return []


def build_cache(tickers: Iterable[str], *,
                  cache_path: Path = CACHE_PATH,
                  max_age_days: int = 7,
                  use_yfinance_fallback: bool = True,
                  delay_sec: float = FINVIZ_DELAY_SEC,
                  progress_every: int = 50) -> dict[str, dict]:
    """Refresh stale entries in the float cache. Idempotent — already-
    fresh entries are left untouched. Persists after every progress
    interval so a Ctrl-C mid-run doesn't lose all work."""
    import requests
    cache = load_cache(cache_path)
    tickers = list(tickers)
    to_fetch = [t for t in tickers
                  if _is_stale(cache.get(t, {}), max_age_days)]
    log.info("Universe: %d tickers (%d need refresh, %d still fresh)",
              len(tickers), len(to_fetch), len(tickers) - len(to_fetch))
    if not to_fetch:
        return cache
    session = requests.Session()
    t_start = time.monotonic()
    n_finviz = 0
    n_yfinance = 0
    n_failed = 0
    for i, ticker in enumerate(to_fetch, 1):
        t_call = time.monotonic()
        rec = lookup_one(ticker, session=session,
                           use_yfinance_fallback=use_yfinance_fallback)
        cache[ticker] = rec
        if rec["source"] == "finviz":
            n_finviz += 1
        elif rec["source"] == "yfinance":
            n_yfinance += 1
        else:
            n_failed += 1
        # Pace
        elapsed = time.monotonic() - t_call
        if elapsed < delay_sec:
            time.sleep(delay_sec - elapsed)
        if i % progress_every == 0:
            save_cache(cache, cache_path)
            rate = i / max(time.monotonic() - t_start, 1e-6)
            eta_min = (len(to_fetch) - i) / max(rate, 1e-6) / 60
            log.info("Progress: %d/%d  finviz=%d yfinance=%d failed=%d "
                      "rate=%.2f/s ETA=%.1fmin",
                      i, len(to_fetch), n_finviz, n_yfinance, n_failed,
                      rate, eta_min)
    save_cache(cache, cache_path)
    elapsed_total = time.monotonic() - t_start
    log.info("DONE: %d processed in %.1fs — finviz=%d yfinance=%d failed=%d",
              len(to_fetch), elapsed_total, n_finviz, n_yfinance, n_failed)
    return cache


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", type=str, default=None,
                     help="comma-separated explicit ticker list "
                          "(overrides candidates.parquet)")
    ap.add_argument("--limit", type=int, default=None,
                     help="cap universe size for smoke testing")
    ap.add_argument("--max-age", type=int, default=7,
                     help="re-fetch cache entries older than N days "
                          "(default 7)")
    ap.add_argument("--no-yfinance", action="store_true",
                     help="disable yfinance fallback (Finviz-only, faster)")
    ap.add_argument("--delay", type=float, default=FINVIZ_DELAY_SEC,
                     help="seconds between Finviz calls (default 1.5)")
    ap.add_argument("--cache-path", type=str, default=str(CACHE_PATH),
                     help="cache file location")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")
                    if t.strip()]
        log.info("Universe: %d tickers from --tickers", len(tickers))
    else:
        tickers = load_universe_tickers()
        log.info("Universe: %d tickers from candidates.parquet",
                  len(tickers))
    if args.limit:
        tickers = tickers[: args.limit]
        log.info("Limited to %d tickers (--limit)", len(tickers))
    if not tickers:
        log.error("Universe empty — nothing to do")
        return 1
    cache_path = Path(args.cache_path)
    build_cache(tickers,
                  cache_path=cache_path,
                  max_age_days=args.max_age,
                  use_yfinance_fallback=not args.no_yfinance,
                  delay_sec=args.delay)
    # Summary
    cache = load_cache(cache_path)
    n_with_float = sum(1 for r in cache.values()
                        if r.get("float_shares") is not None)
    n_smallcap = sum(1 for r in cache.values()
                      if (r.get("float_shares") or 0) > 0
                      and r["float_shares"] < 10_000_000)
    log.info("Cache summary: %d entries, %d with float (%.1f%%), "
              "%d are <10M-float small-caps",
              len(cache), n_with_float,
              100 * n_with_float / max(len(cache), 1), n_smallcap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
