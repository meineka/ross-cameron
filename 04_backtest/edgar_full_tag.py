"""edgar_full_tag.py — alle Candidate-Days gegen EDGAR 8-K taggen.

Schreibt candidates_with_catalyst_full.parquet mit zusätzlicher Spalte has_8k.
Rate-limit-konform (max 10 req/sec laut SEC), mit User-Agent-Header.
"""
import sys, io, time, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
import pandas as pd
import requests
from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

D = Path(__file__).resolve().parent / "data_pilot"
SEC_USER_AGENT = "ross-cameron-backtest szymon@example.com"
WINDOW_DAYS = 1


def has_8k(ticker: str, date: pd.Timestamp) -> bool | None:
    try:
        url = "https://www.sec.gov/cgi-bin/browse-edgar"
        params = {
            "action": "getcompany",
            "CIK": ticker,
            "type": "8-K",
            "dateb": (pd.Timestamp(date) + pd.Timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d"),
            "datea": (pd.Timestamp(date) - pd.Timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d"),
            "output": "atom",
        }
        r = requests.get(url, params=params,
                         headers={"User-Agent": SEC_USER_AGENT}, timeout=15)
        if r.status_code != 200:
            return None
        return "<entry>" in r.text
    except Exception as e:
        log.debug("err %s %s: %s", ticker, date, e)
        return None


def main():
    cands = pd.read_parquet(D / "candidates.parquet")
    intraday = pd.read_parquet(D / "intraday_5m.parquet")
    # Identify time-col
    tc = next((c for c in intraday.columns if "time" in c.lower() or "date" in c.lower()), None)
    intraday[tc] = pd.to_datetime(intraday[tc], utc=True, errors="coerce")
    intraday = intraday.dropna(subset=[tc])
    intraday["session_date"] = intraday[tc].dt.tz_convert("America/New_York").dt.date
    # Only candidates that have intraday coverage
    pairs_with_intraday = set(zip(intraday["ticker"], intraday["session_date"]))
    cands["date_only"] = pd.to_datetime(cands["date"]).dt.date
    mask = [(t, d) in pairs_with_intraday for t, d in zip(cands["ticker"], cands["date_only"])]
    cands_filtered = cands[mask].reset_index(drop=True).copy()
    log.info("Tagging %d candidates with intraday coverage…", len(cands_filtered))

    has_flag = []
    for r in tqdm(cands_filtered.itertuples(), total=len(cands_filtered)):
        has_flag.append(has_8k(r.ticker, r.date))
        time.sleep(0.12)  # ~8 req/s, well within SEC's 10 req/s limit
    cands_filtered["has_8k"] = has_flag
    cands_filtered.to_parquet(D / "candidates_with_catalyst_full.parquet")
    rate = pd.Series(has_flag).fillna(False).mean()
    log.info("DONE. 8-K match-rate: %.1f%%", rate * 100)


if __name__ == "__main__":
    main()
