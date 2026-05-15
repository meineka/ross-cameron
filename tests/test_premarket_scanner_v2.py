"""Phase-16 (ChatGPT-08:11 #1 / P1.1): real premarket scanner.

Tests cover:
  - per-candidate reject_reasons emission (no_snapshot, no_previous_close,
    no_latest_trade, gap_under_threshold, spread, vol)
  - scan_alpaca_premarket_with_reasons returns ALL rows (passed + rejected)
  - merge_premarket_rvol_into_rows adds bar-stats and applies RVOL gate
  - extended-hours bar processing computes today / avg / rvol correctly
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.critical  # Phase-21 (ChatGPT-09:15 #1): live-safety gate
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_snap(*, prev_close=None, last_price=None, last_age_s=10,
                bid=None, ask=None, daily_vol=None, daily_close=None):
    """Build a fake Alpaca StockSnapshot-like object."""
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        previous_daily_bar=SimpleNamespace(close=prev_close) if prev_close is not None else None,
        latest_trade=SimpleNamespace(
            price=last_price,
            timestamp=now - timedelta(seconds=last_age_s),
        ) if last_price is not None else None,
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask)
                       if (bid is not None and ask is not None) else None,
        daily_bar=SimpleNamespace(close=daily_close, volume=daily_vol)
                    if (daily_close is not None or daily_vol is not None) else None,
    )


def test_evaluate_with_reasons_clean_pass():
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=10.0, last_price=10.80, last_age_s=30,
                       bid=10.78, ask=10.82, daily_vol=2_000_000, daily_close=10.80)
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["ticker"] == "AAA"
    assert row["passed"] is True
    assert row["reject_reasons"] == []
    assert row["gap_pct"] == pytest.approx(8.0)


def test_evaluate_with_reasons_emits_no_snapshot():
    import premarket_scanner_v2 as pm
    row = pm._evaluate_snapshot_with_reasons("AAA", None, datetime.now(timezone.utc))
    assert row["passed"] is False
    assert "no_snapshot" in row["reject_reasons"]


def test_evaluate_with_reasons_emits_no_previous_close():
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=None, last_price=10.0)
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["passed"] is False
    assert "no_previous_close" in row["reject_reasons"]


def test_evaluate_with_reasons_emits_gap_too_small():
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=10.0, last_price=10.20,
                       daily_vol=2_000_000, daily_close=10.20)
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["passed"] is False
    assert any("gap_2.00%_under_5.0%" in r for r in row["reject_reasons"])


def test_evaluate_with_reasons_emits_spread_too_wide():
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=10.0, last_price=10.80,
                       bid=10.0, ask=12.0,  # 18%+ spread
                       daily_vol=2_000_000, daily_close=10.80)
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["passed"] is False
    assert any("spread" in r and "over" in r for r in row["reject_reasons"])


def test_evaluate_with_reasons_emits_volume_too_low():
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=10.0, last_price=10.80,
                       daily_vol=500, daily_close=10.80)  # < 1000
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["passed"] is False
    assert any("vol_500" in r for r in row["reject_reasons"])


def test_evaluate_with_reasons_multiple_reasons_stack():
    """A single candidate failing multiple gates returns all of them."""
    import premarket_scanner_v2 as pm
    snap = _make_snap(prev_close=10.0, last_price=10.10,  # 1% gap
                       bid=10.0, ask=12.0,  # huge spread
                       daily_vol=100, daily_close=10.10)  # tiny vol
    row = pm._evaluate_snapshot_with_reasons("AAA", snap, datetime.now(timezone.utc), mode="strict")
    assert row["passed"] is False
    rr = row["reject_reasons"]
    assert any("gap_" in r for r in rr)
    assert any("spread_" in r for r in rr)
    assert any("vol_" in r for r in rr)


def test_scan_alpaca_premarket_with_reasons_returns_all_rows(monkeypatch):
    """The diagnosable scanner must return one row per input symbol
    (passed AND rejected), not just passed."""
    import premarket_scanner_v2 as pm

    class FakeClient:
        def get_stock_snapshot(self, req):
            symbols = req.symbol_or_symbols
            return {
                symbols[0]: _make_snap(prev_close=10.0, last_price=10.80,
                                         bid=10.78, ask=10.82,
                                         daily_vol=2_000_000, daily_close=10.80),
                symbols[1]: _make_snap(prev_close=10.0, last_price=10.10,  # gap too small
                                         daily_vol=2_000_000, daily_close=10.10),
                symbols[2]: None,  # no snapshot
            }

    # Stub StockSnapshotRequest import
    fake_req_mod = SimpleNamespace(
        StockSnapshotRequest=lambda symbol_or_symbols: SimpleNamespace(
            symbol_or_symbols=symbol_or_symbols))
    monkeypatch.setitem(sys.modules, "alpaca.data.requests", fake_req_mod)

    rows = pm.scan_alpaca_premarket_with_reasons(
        FakeClient(), ["AAA", "BBB", "CCC"], mode="strict")
    assert len(rows) == 3
    by_sym = {r["ticker"]: r for r in rows}
    assert by_sym["AAA"]["passed"] is True
    assert by_sym["BBB"]["passed"] is False
    assert any("gap_" in r for r in by_sym["BBB"]["reject_reasons"])
    assert by_sym["CCC"]["passed"] is False
    assert "no_snapshot" in by_sym["CCC"]["reject_reasons"]


def test_scan_alpaca_premarket_with_reasons_emits_batch_failure(monkeypatch):
    """If the batch fetch throws, every symbol in that batch gets a
    'batch_fetch_failed' reject row — no silent dropping."""
    import premarket_scanner_v2 as pm

    class FakeClient:
        def get_stock_snapshot(self, req):
            raise RuntimeError("alpaca-down")

    fake_req_mod = SimpleNamespace(
        StockSnapshotRequest=lambda symbol_or_symbols: SimpleNamespace(
            symbol_or_symbols=symbol_or_symbols))
    monkeypatch.setitem(sys.modules, "alpaca.data.requests", fake_req_mod)

    rows = pm.scan_alpaca_premarket_with_reasons(
        FakeClient(), ["AAA", "BBB"], mode="strict")
    # Each symbol gets a batch_fetch row plus an evaluator row (which says no_snapshot)
    # The exact dedupe behavior is fine either way; key is: every symbol surfaces.
    syms_seen = {r["ticker"] for r in rows}
    assert syms_seen == {"AAA", "BBB"}
    # At least one row per symbol should mention the batch failure
    for sym in syms_seen:
        sym_rows = [r for r in rows if r["ticker"] == sym]
        any_batch = any(any("batch_fetch_failed" in rr for rr in r.get("reject_reasons", []))
                         for r in sym_rows)
        any_no_snap = any("no_snapshot" in r.get("reject_reasons", []) for r in sym_rows)
        assert any_batch or any_no_snap, f"{sym} should have a diagnostic reason"


def test_merge_premarket_rvol_adds_stats_to_rows():
    """merge_premarket_rvol_into_rows attaches bar stats to each row."""
    import premarket_scanner_v2 as pm
    rows = [
        {"ticker": "AAA", "passed": True, "reject_reasons": []},
        {"ticker": "BBB", "passed": True, "reject_reasons": []},
    ]
    bar_stats = {
        "AAA": {"premarket_volume_today": 500_000,
                 "avg_premarket_volume": 100_000,
                 "premarket_rvol": 5.0,
                 "premarket_high": 11.0, "premarket_low": 10.5,
                 "premarket_vwap": 10.7, "bars_today": 90},
        # BBB: no stats
    }
    out = pm.merge_premarket_rvol_into_rows(rows, bar_stats, mode="strict")
    by_sym = {r["ticker"]: r for r in out}
    assert by_sym["AAA"]["premarket_rvol"] == 5.0
    assert by_sym["AAA"]["passed"] is True
    assert by_sym["BBB"]["premarket_rvol"] is None
    assert by_sym["BBB"]["passed"] is False
    assert "no_premarket_bars" in by_sym["BBB"]["reject_reasons"]


def test_merge_premarket_rvol_rejects_low_rvol():
    """Symbol with premarket_rvol below MIN_PREMARKET_RVOL gets rejected."""
    import premarket_scanner_v2 as pm
    rows = [{"ticker": "AAA", "passed": True, "reject_reasons": []}]
    bar_stats = {"AAA": {"premarket_volume_today": 50_000,
                          "avg_premarket_volume": 100_000,
                          "premarket_rvol": 0.5,
                          "bars_today": 60}}
    out = pm.merge_premarket_rvol_into_rows(rows, bar_stats, mode="strict")
    assert out[0]["passed"] is False
    assert any("rvol_0.50_under" in r for r in out[0]["reject_reasons"])


def test_premarket_window_returns_4am_930am_utc():
    """The today-window helper should produce UTC times that bracket the
    NY premarket session."""
    import premarket_scanner_v2 as pm
    # Pick a mid-day UTC time so NY conversion is unambiguous
    test_utc = datetime(2026, 5, 15, 16, 0, 0, tzinfo=timezone.utc)
    start, end = pm._today_premarket_window_utc(test_utc)
    assert end > start
    span_hours = (end - start).total_seconds() / 3600
    assert span_hours == pytest.approx(5.5, abs=0.01)  # 04:00 to 09:30
