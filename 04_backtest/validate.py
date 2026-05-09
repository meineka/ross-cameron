"""validate.py - sanity-check fuer Bootstrap-Outputs."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
import pandas as pd

D = Path(__file__).resolve().parent / "data_pilot"

def section(title):
    print("\n" + "=" * 60); print(title); print("=" * 60)

def main():
    # ── Universe ──
    section("UNIVERSE")
    u = pd.read_parquet(D / "universe.parquet")
    print(f"  rows: {len(u)}")
    print(f"  sample: {u['ticker'].head(10).tolist()}")
    assert len(u) > 1000, "Universe too small"

    # ── Daily ──
    section("DAILY-BARS")
    d = pd.read_parquet(D / "daily.parquet")
    print(f"  rows: {len(d):,}")
    print(f"  unique tickers: {d['ticker'].nunique():,}")
    print(f"  date range: {d['date'].min()} → {d['date'].max()}")
    print(f"  columns: {d.columns.tolist()}")
    print(f"  NaN per col:\n{d.isna().sum()}")
    assert len(d) > 100_000, "Daily too small"

    # ── Candidates ──
    section("CAMERON-CANDIDATES")
    c = pd.read_parquet(D / "candidates.parquet")
    print(f"  rows: {len(c):,}")
    print(f"  unique tickers: {c['ticker'].nunique()}")
    print(f"  date range: {c['date'].min()} → {c['date'].max()}")
    print(f"  Top 10 by intraday_pct:")
    print(c.nlargest(10, 'intraday_pct')[['ticker','date','close','intraday_pct','rvol_proxy']].to_string())
    print(f"\n  Distribution intraday_pct:\n{c['intraday_pct'].describe()}")
    print(f"\n  Distribution rvol_proxy:\n{c['rvol_proxy'].describe()}")
    print(f"\n  Distribution close-Preis:\n{c['close'].describe()}")

    # ── Intraday ──
    section("INTRADAY 5-MIN")
    i = pd.read_parquet(D / "intraday_5m.parquet")
    print(f"  rows: {len(i):,}")
    print(f"  unique tickers: {i['ticker'].nunique()}")
    print(f"  columns: {i.columns.tolist()}")
    # Find time column
    tc = next((c for c in i.columns if "time" in c.lower() or "date" in c.lower()), None)
    print(f"  time-col guessed: {tc}")
    if tc:
        i[tc] = pd.to_datetime(i[tc], utc=True, errors="coerce")
        print(f"  time range: {i[tc].min()} → {i[tc].max()}")
        print(f"  rows per ticker (sample):")
        print(i.groupby('ticker').size().describe())
    print(f"  NaN per col:\n{i.isna().sum()}")

    # Sample one ticker, one day
    section("SAMPLE: Top-Mover Ticker, ein Tag")
    top = c.nlargest(1, 'intraday_pct').iloc[0]
    print(f"  picking: {top['ticker']} on {top['date']} ({top['intraday_pct']:.1f}% move)")
    sub = i[i['ticker'] == top['ticker']]
    if len(sub):
        print(f"  total intraday rows for ticker: {len(sub)}")
        print(sub.head(15).to_string())

    section("VALIDATION DONE")
    print("OK" if len(c) > 50 and len(i) > 1000 else "WARN: low data — check filter")

if __name__ == "__main__":
    main()
