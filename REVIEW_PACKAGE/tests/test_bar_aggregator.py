"""Tests für 1-Min → N-Min Bar-Aggregator.

Audit-Iter post-2026-05-13 (Option A): WS liefert 1-Min, Cameron-Setup
braucht 5-Min. Aggregator schließt die Lücke.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bar(ts: datetime, o=10.0, h=10.5, l=9.5, c=10.2, v=1000):
    return {"open": o, "high": h, "low": l, "close": c,
            "volume": v, "timestamp": ts}


# ─── Bucket-Boundaries (wall-clock) ──────────────────────────────────────────
def test_bucket_boundary_floors_to_5min():
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    # 9:32 → 9:30 bucket
    ts = datetime(2026, 5, 13, 9, 32, 0)
    assert agg._bucket_start(ts).minute == 30
    # 9:34:59 → still 9:30 bucket
    ts2 = datetime(2026, 5, 13, 9, 34, 59)
    assert agg._bucket_start(ts2).minute == 30
    # 9:35:00 → 9:35 bucket
    ts3 = datetime(2026, 5, 13, 9, 35, 0)
    assert agg._bucket_start(ts3).minute == 35
    # 9:37 → 9:35
    ts4 = datetime(2026, 5, 13, 9, 37, 0)
    assert agg._bucket_start(ts4).minute == 35


def test_bucket_minutes_must_divide_60():
    from bar_aggregator import BarAggregator
    with pytest.raises(ValueError):
        BarAggregator(bucket_minutes=7)  # 60 % 7 != 0


def test_bucket_minutes_positive():
    from bar_aggregator import BarAggregator
    with pytest.raises(ValueError):
        BarAggregator(bucket_minutes=0)
    with pytest.raises(ValueError):
        BarAggregator(bucket_minutes=-5)


# ─── Aggregation: 5 bars in einen bucket ─────────────────────────────────────
def test_first_4_bars_no_emit():
    """Innerhalb eines Buckets: kein emit."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    for i in range(4):
        out = agg.add("X", _bar(base + timedelta(minutes=i)))
        assert out is None, f"unexpected emit at minute {i}"


def test_5th_minute_in_new_bucket_emits_first():
    """1-Min-Bar in 9:35-bucket trigger emit von 9:30-9:34 als 5-Min-Bar."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    # Add 5 bars: 9:30, 9:31, 9:32, 9:33, 9:34 → all in 9:30 bucket
    for i in range(5):
        out = agg.add("X", _bar(base + timedelta(minutes=i),
                                 o=10+i*0.1, h=10+i*0.1+0.05,
                                 l=10+i*0.1-0.05, c=10+i*0.1+0.03,
                                 v=1000+i*100))
        assert out is None
    # 6th bar at 9:35 → in 9:35 bucket → emits 9:30-9:34 aggregated
    out = agg.add("X", _bar(base + timedelta(minutes=5)))
    assert out is not None
    assert out["open"] == 10.0  # 9:30 open
    # high should be max of 5 bars: 10.0+0.4+0.05 = 10.45
    assert abs(out["high"] - 10.45) < 0.001
    assert out["low"] == 9.95  # 9:30 low
    # close = 9:34's close = 10+0.4+0.03 = 10.43
    assert abs(out["close"] - 10.43) < 0.001
    # volume = sum 1000+1100+1200+1300+1400 = 6000
    assert out["volume"] == 6000.0
    # timestamp = bucket start = 9:30
    assert out["timestamp"].minute == 30


# ─── Partial buckets ─────────────────────────────────────────────────────────
def test_3_bars_in_bucket_then_jump_emits_partial():
    """Wenn der nächste Bar einen Bucket überspringt, wird das partial emitted."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("X", _bar(base + timedelta(minutes=0)))
    agg.add("X", _bar(base + timedelta(minutes=1)))
    agg.add("X", _bar(base + timedelta(minutes=2)))
    # Skip to 9:40 → emits 9:30-9:32 (partial)
    out = agg.add("X", _bar(base + timedelta(minutes=10)))
    assert out is not None
    assert out["timestamp"].minute == 30


def test_flush_emits_partial():
    """Manual flush für HARD_FLAT-Path."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("X", _bar(base, c=10.5))
    agg.add("X", _bar(base + timedelta(minutes=1), c=10.7))
    out = agg.flush("X")
    assert out is not None
    assert out["close"] == 10.7
    # Buffer leer
    assert agg.buffer_size("X") == 0


def test_flush_empty_returns_none():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    assert agg.flush("UNKNOWN") is None


def test_flush_all_emits_all_partials():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("A", _bar(base, c=10.0))
    agg.add("B", _bar(base, c=20.0))
    out = agg.flush_all()
    assert set(out.keys()) == {"A", "B"}
    assert out["A"]["close"] == 10.0
    assert out["B"]["close"] == 20.0


# ─── Multi-Symbol Isolation ──────────────────────────────────────────────────
def test_symbols_isolated():
    """Bars von Symbol A dürfen Symbol B's bucket nicht beeinflussen."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("A", _bar(base, c=10.0, v=500))
    agg.add("B", _bar(base, c=20.0, v=2000))
    # Skip A directly to next bucket
    out_a = agg.add("A", _bar(base + timedelta(minutes=5), c=11.0))
    # A's aggregation only contains A's bar
    assert out_a is not None
    assert out_a["close"] == 10.0
    assert out_a["volume"] == 500.0
    # B still has 1 buffered bar
    assert agg.buffer_size("B") == 1


# ─── Edge: bar without timestamp ─────────────────────────────────────────────
def test_bar_without_timestamp_returns_none():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    out = agg.add("X", {"open": 10, "high": 11, "low": 9, "close": 10, "volume": 100})
    assert out is None


def test_bar_with_invalid_timestamp_returns_none():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    out = agg.add("X", {"open": 10, "high": 11, "low": 9, "close": 10,
                          "volume": 100, "timestamp": "garbage"})
    assert out is None


# ─── Reset ───────────────────────────────────────────────────────────────────
def test_reset_clears_specific_symbol():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("A", _bar(base))
    agg.add("B", _bar(base))
    agg.reset("A")
    assert agg.buffer_size("A") == 0
    assert agg.buffer_size("B") == 1


def test_reset_clears_all():
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    base = datetime(2026, 5, 13, 9, 30, 0)
    agg.add("A", _bar(base))
    agg.add("B", _bar(base))
    agg.reset()
    assert agg.buffer_size("A") == 0
    assert agg.buffer_size("B") == 0


# ─── OHLCV Math ──────────────────────────────────────────────────────────────
def test_ohlcv_math_is_correct():
    """Klassischer OHLCV merge."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator()
    base = datetime(2026, 5, 13, 9, 30, 0)
    bars_data = [
        # (o, h, l, c, v)
        (10.00, 10.20, 9.95, 10.10, 1000),  # 9:30
        (10.10, 10.40, 10.05, 10.30, 1500),  # 9:31
        (10.30, 10.35, 10.20, 10.25, 800),   # 9:32
        (10.25, 10.50, 10.00, 10.45, 2000),  # 9:33 (high = 10.50, low = 10.00)
        (10.45, 10.60, 10.40, 10.55, 1200),  # 9:34
    ]
    for i, (o, h, l, c, v) in enumerate(bars_data):
        agg.add("X", _bar(base + timedelta(minutes=i), o=o, h=h, l=l, c=c, v=v))
    # Trigger emit
    out = agg.add("X", _bar(base + timedelta(minutes=5)))
    assert out["open"] == 10.00   # first
    assert out["close"] == 10.55  # last
    assert out["high"] == 10.60   # max across all
    assert out["low"] == 9.95     # min across all
    assert out["volume"] == 6500.0  # sum


# ─── 9:30 RTH-Open: erstes 5-Min-Bar fertig erst um 9:35 ─────────────────────
def test_first_5min_bar_after_open_emits_at_935():
    """Realistic: 9:30 RTH open. Erste 5 1-Min-Bars (9:30-9:34) sind im
    9:30-Bucket. Bar bei 9:35 trigger emit."""
    from bar_aggregator import BarAggregator
    agg = BarAggregator(bucket_minutes=5)
    base = datetime(2026, 5, 13, 9, 30, 0)
    emits = []
    for minute in range(15):  # 9:30 bis 9:44 = 15 1-Min-Bars
        bar = _bar(base + timedelta(minutes=minute))
        out = agg.add("X", bar)
        if out:
            emits.append(out)
    # Sollten 2 emitted bars haben: 9:30 (bei minute 5 trigger), 9:35 (bei minute 10)
    assert len(emits) == 2
    assert emits[0]["timestamp"].minute == 30
    assert emits[1]["timestamp"].minute == 35
