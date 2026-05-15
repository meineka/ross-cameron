"""Phase-32: aligned_scan_start must never return a time in the past.

ROOT CAUSE found via live diagnosis on 2026-05-15: the SLOW rescan was
firing every ~3 seconds (instead of every 2 minutes) because
SCAN_HEAD_START_SLOW_SEC=180 with RESCAN_SLOW_INTERVAL_MIN=2 caused
aligned_scan_start() to compute a next-start IN THE PAST.

The cascading effect was:
  scan -> watchlist update -> WS resubscribe -> auth -> connection-limit-exceeded
  ... 3 seconds later, again ... and again ... and again

Fix: clamp head_start to (period * 60 - 30) and advance the boundary
forward in a loop until start > now.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))

NY = timezone(timedelta(hours=-4))


def test_aligned_scan_start_never_returns_past_time():
    """The regression: with head_start >= period*60 the old code
    returned past times. Must now always return > now."""
    from bot import aligned_scan_start
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    # Period 2 min, head_start 180s — the broken combo
    start = aligned_scan_start(now, period_min=2, head_start_sec=180)
    assert start > now, f"start {start} must be > now {now}"


def test_aligned_scan_start_clamps_huge_head_start():
    """Even an absurd head_start must not break the schedule."""
    from bot import aligned_scan_start
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    start = aligned_scan_start(now, period_min=1, head_start_sec=3600)
    assert start > now
    # Should be at most 1 period away
    assert start - now <= timedelta(minutes=1)


def test_aligned_scan_start_5min_period_legacy_case():
    """The original use case (5-min period, 180s head_start) must still
    work — finish at :05, :10, :15, start at :02, :07, :12, ..."""
    from bot import aligned_scan_start
    now = datetime(2026, 5, 15, 15, 32, 10, tzinfo=NY)
    start = aligned_scan_start(now, period_min=5, head_start_sec=180)
    # next 5-min boundary is 15:35, start is 15:32:00
    # But now is 15:32:10 — past 15:32:00 — so next is 15:37:00
    assert start == datetime(2026, 5, 15, 15, 37, 0, tzinfo=NY)


def test_aligned_scan_start_2min_period_finishes_just_before_boundary():
    """With the new 2-min cadence + 90s head_start, scan must START at
    :30 seconds before each even minute (so finishes by :00)."""
    from bot import aligned_scan_start
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    start = aligned_scan_start(now, period_min=2, head_start_sec=90)
    # Next 2-min boundary after 15:55:25 is 15:56:00.
    # Start = 15:56:00 - 90s = 15:54:30 — past now → advance to 15:58:00
    # Start = 15:58:00 - 90s = 15:56:30
    assert start == datetime(2026, 5, 15, 15, 56, 30, tzinfo=NY)
    assert start > now


def test_aligned_scan_start_zero_head_start():
    """head_start=0 means start AT the boundary, not before."""
    from bot import aligned_scan_start
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    start = aligned_scan_start(now, period_min=2, head_start_sec=0)
    assert start == datetime(2026, 5, 15, 15, 56, 0, tzinfo=NY)


def test_bot_config_head_start_not_larger_than_period():
    """Live-bot config sanity: SLOW head_start < SLOW interval, else
    the rescan storm bug re-emerges."""
    import bot
    assert bot.SCAN_HEAD_START_SLOW_SEC < bot.RESCAN_SLOW_INTERVAL_MIN * 60, (
        f"SCAN_HEAD_START_SLOW_SEC={bot.SCAN_HEAD_START_SLOW_SEC}s must be "
        f"< RESCAN_SLOW_INTERVAL_MIN*60={bot.RESCAN_SLOW_INTERVAL_MIN*60}s; "
        f"otherwise SLOW rescan fires every loop iter (Phase-32 regression)"
    )
    assert bot.SCAN_HEAD_START_FAST_SEC < bot.RESCAN_FAST_INTERVAL_MIN * 60
