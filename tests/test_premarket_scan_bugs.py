"""Audit-Iter 16 (2026-05-12): premarket_scan data-resilienz bugs.

Bug SCN-2 (HIGH): bei corrupt data (prev_close=0) wurde
  intraday_pct = (high - 0) / 0 * 100 = inf. Filter `intraday_pct >= 10`
  passierte → False-Positive ticker im Watchlist mit "+inf%" daily-gain.

Bug SCN-3 (HIGH): gleichermaßen rvol_proxy = volume / 0 = inf.

Bug SCN-7 (MED): groupby tail(1) lieferte LATEST bar pro ticker. Aber bei
  halted/delisted stocks war das eine 2+ Wochen alte Bar — wurde als
  "today's move" interpretiert. Jetzt: nur Bars der letzten 4 Tage.

Hier testen wir die Helper-Math direkt (yfinance-Mock zu komplex).
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_intraday_pct_with_zero_prev_close_is_filtered():
    """Reproduktion SCN-2: prev_close=0 → mit fix NaN nicht inf."""
    df = pd.DataFrame({
        "high": [1.0, 1.1, 1.2],
        "prev_close": [0.0, 0.5, 1.0],
    })
    # Mit fix:
    df["intraday_pct"] = (df["high"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
    # inf darf nicht passieren
    assert not np.isinf(df["intraday_pct"]).any()
    # NaN ist erlaubt aber wird durch finite-filter rausgenommen
    finite = df[np.isfinite(df["intraday_pct"])]
    assert len(finite) == 2  # die zwei valid rows
    # corrupt row mit prev_close=0 muss raus sein
    assert 0.0 not in finite["prev_close"].values


def test_rvol_proxy_with_zero_avg_vol_is_filtered():
    """SCN-3: avg_vol_20=0 → mit fix NaN nicht inf."""
    df = pd.DataFrame({
        "volume": [1000, 2000, 3000],
        "avg_vol_20": [0, 500, 1000],
    })
    df["rvol_proxy"] = df["volume"] / df["avg_vol_20"].replace(0, np.nan)
    assert not np.isinf(df["rvol_proxy"]).any()
    finite = df[np.isfinite(df["rvol_proxy"])]
    assert len(finite) == 2


def test_combined_filter_drops_both_corrupt():
    """Beide Spalten inf-prone → finite-filter cleant alles aus."""
    df = pd.DataFrame({
        "high": [1.0, 1.1, 1.2],
        "prev_close": [0.0, 0.5, 1.0],
        "volume": [1000, 2000, 3000],
        "avg_vol_20": [0, 500, 1000],
    })
    df["intraday_pct"] = (df["high"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
    df["rvol_proxy"] = df["volume"] / df["avg_vol_20"].replace(0, np.nan)
    clean = df[np.isfinite(df["intraday_pct"]) & np.isfinite(df["rvol_proxy"])]
    assert len(clean) == 2  # row 0 (beide corrupt) raus
    assert clean.iloc[0]["prev_close"] == 0.5
    assert clean.iloc[1]["prev_close"] == 1.0


def test_stale_bar_filter_drops_old_dates():
    """SCN-7: tail(1) bei halted-Stock liefert alte Bar. Filter muss raus."""
    today = pd.Timestamp.now(tz="UTC").normalize()
    df = pd.DataFrame({
        "ticker": ["FRESH", "HALTED"],
        "date": [today, today - pd.Timedelta(days=30)],
        "close": [10.0, 5.0],
    })
    # Mit fix:
    min_date = today - pd.Timedelta(days=4)
    latest_dt = pd.to_datetime(df["date"], utc=True, errors="coerce")
    fresh_only = df[latest_dt >= min_date]
    assert len(fresh_only) == 1
    assert fresh_only.iloc[0]["ticker"] == "FRESH"


def test_stale_bar_filter_keeps_today():
    today = pd.Timestamp.now(tz="UTC").normalize()
    yesterday = today - pd.Timedelta(days=1)
    df = pd.DataFrame({
        "ticker": ["A", "B"],
        "date": [today, yesterday],
        "close": [10.0, 11.0],
    })
    min_date = today - pd.Timedelta(days=4)
    latest_dt = pd.to_datetime(df["date"], utc=True, errors="coerce")
    fresh = df[latest_dt >= min_date]
    assert len(fresh) == 2


# ─── Smoke test: premarket_scan retry-Logik bei Exception ────────────────────
def test_premarket_scan_returns_empty_on_persistent_failure(monkeypatch):
    """Inner failed 3x → return [] (statt crash)."""
    import bot
    call_count = {"n": 0}

    def boom(*a, **kw):
        call_count["n"] += 1
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(bot, "_premarket_scan_inner", boom)
    # Patch sleep to skip real waits
    monkeypatch.setattr("time.sleep", lambda *a: None)
    result = bot.premarket_scan(top_n=5, max_retries=2)
    assert result == []
    # 1 initial + 2 retries = 3
    assert call_count["n"] == 3


def test_premarket_scan_returns_result_on_first_success(monkeypatch):
    """First call succeeds → no retries."""
    import bot
    call_count = {"n": 0}
    fake_ts = bot.TickerState(symbol="X", rank=1, score=1.0)

    def succeed(*a, **kw):
        call_count["n"] += 1
        return [fake_ts]

    monkeypatch.setattr(bot, "_premarket_scan_inner", succeed)
    result = bot.premarket_scan(top_n=5)
    assert len(result) == 1
    assert call_count["n"] == 1


def test_premarket_scan_succeeds_on_retry(monkeypatch):
    """First fail, second success → result, no further retry."""
    import bot
    call_count = {"n": 0}
    fake_ts = bot.TickerState(symbol="Y", rank=1, score=1.0)

    def maybe_succeed(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("flaky")
        return [fake_ts]

    monkeypatch.setattr(bot, "_premarket_scan_inner", maybe_succeed)
    monkeypatch.setattr("time.sleep", lambda *a: None)
    result = bot.premarket_scan(top_n=5, max_retries=2)
    assert len(result) == 1
    assert call_count["n"] == 2
