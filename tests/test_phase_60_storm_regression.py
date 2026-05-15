"""Phase-60: explicit WS reconnect-throttle + scan-storm regression
tests addressing ChatGPT P1 follow-up.

Two scenarios that historically caused the 2026-05-15 stall and
must NEVER silently re-emerge:

1. Cascading SLOW rescan storm (Phase-32 root-cause): if
   aligned_scan_start() returns a time in the past, the bot fires
   intraday_rescan in every loop iteration → constant WS re-subscribe
   → Alpaca conn-limit → spiral.

2. WS reconnect throttle (Phase-31/41/42 root-cause): on
   conn-limit-exceeded, the bot must NOT hammer Alpaca with auth
   attempts faster than the global cool-down allows. Spec: <=2
   attempts per minute under sustained block.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. SLOW rescan must NEVER fire more than once per period ─────────────

def test_aligned_scan_start_with_phase32_config_does_not_storm():
    """Repro of 2026-05-15 17:30 incident: SCAN_HEAD_START_SLOW_SEC=180
    + RESCAN_SLOW_INTERVAL_MIN=2 made aligned_scan_start() return past
    timestamps → rescan fired every 3 seconds → conn-limit cascade.

    Phase-32 fix: head_start clamped to period*60-30; while-loop
    advances boundary until start > now.

    Asserts: starting from a fresh now, the next scan time is ALWAYS
    in the future, even with adversarial config."""
    from bot import aligned_scan_start
    NY = timezone(timedelta(hours=-4))
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    for head_start in (5, 30, 90, 180, 300, 3600):
        for period in (1, 2, 5, 10):
            t = aligned_scan_start(now, period_min=period,
                                    head_start_sec=head_start)
            assert t > now, (
                f"REGRESSION: aligned_scan_start returned past time! "
                f"period={period}min head_start={head_start}s "
                f"now={now} returned={t}"
            )


def test_aligned_scan_start_idempotent_when_called_back_to_back():
    """If called twice in quick succession, the second result should
    be the SAME boundary as the first (or later). Never earlier."""
    from bot import aligned_scan_start
    NY = timezone(timedelta(hours=-4))
    now = datetime(2026, 5, 15, 15, 55, 25, tzinfo=NY)
    t1 = aligned_scan_start(now, period_min=2, head_start_sec=90)
    t2 = aligned_scan_start(now, period_min=2, head_start_sec=90)
    assert t1 == t2, f"non-deterministic: {t1} vs {t2}"


# ─── 2. WS reconnect throttle ─────────────────────────────────────────────

def test_ws_patch_imposes_at_least_5_second_initial_retry():
    """Phase-41 schedule: first conn-limit retry waits >=5s.
    Phase-42 cool-down: subsequent retries respect 90s module-global
    timer across all StockDataStream instances."""
    import alpaca_ws_patch
    assert alpaca_ws_patch.CONN_LIMIT_SLEEP_SCHEDULE[0] >= 5
    assert alpaca_ws_patch.COOL_DOWN_AFTER_CONN_LIMIT_SEC >= 60


def test_ws_patch_schedule_strictly_increasing():
    """Schedule must be monotonic-non-decreasing — never go BACK to a
    shorter sleep after a longer one, else Alpaca's session-linger
    timer never expires."""
    import alpaca_ws_patch
    sched = alpaca_ws_patch.CONN_LIMIT_SLEEP_SCHEDULE
    for i in range(len(sched) - 1):
        assert sched[i] <= sched[i + 1], (
            f"REGRESSION: schedule non-monotonic at idx {i}: "
            f"{sched[i]} > {sched[i+1]}"
        )


def test_ws_patch_max_sleep_at_least_300s():
    """After persistent failures (consec>=4), sleep must reach at
    least 300s so external operator can react. Less than 5min and
    we're back to spamming."""
    import alpaca_ws_patch
    sched = alpaca_ws_patch.CONN_LIMIT_SLEEP_SCHEDULE
    assert sched[-1] >= 300, (
        f"max-retry-sleep too short: {sched[-1]}s. Need >=300s so "
        f"Alpaca's session-linger always expires between attempts."
    )


# ─── 3. Bot startup sanity ────────────────────────────────────────────────

def test_bot_startup_constants_are_cameron_strict():
    """Phase-51 revert sanity: after Cameron-strict revert, the
    documented values must persist. Any future see-some-trades
    override must NOT be the default."""
    import bot
    assert bot.PRICE_MIN == 2.0
    assert bot.PRICE_MAX == 20.0
    assert bot.DAILY_GAIN_MIN_PCT == 10.0
    assert bot.RVOL_MIN_PROXY == 5.0
    assert bot.FLOAT_MAX_SHARES == 10_000_000
    assert bot.POLE_MIN_MOVE_PCT == 4.0
    assert bot.POLE_TOPPING_TAIL_MAX == 0.5
    assert bot.FLAG_RETRACE_MAX_PCT == 50.0
    assert bot.BREAKOUT_VOL_FACTOR == 1.5
    assert bot.MAX_RISK_PCT == 5.0


def test_bot_aggregation_5m_for_cameron_strict():
    """Cameron primary timeframe is 5m. Phase-36 1m experiment is
    reverted in Phase-51."""
    import bot
    assert bot.BAR_AGGREGATION_MINUTES == 5


# ─── 4. last_no_trade_reason field exists ─────────────────────────────────

def test_day_state_has_last_no_trade_reason():
    """Phase-60 (ChatGPT P1): DayState must expose last_no_trade_reason
    so status.json can surface "why no trade today" without log-grep."""
    from bot import DayState
    d = DayState()
    assert hasattr(d, "last_no_trade_reason")
    assert d.last_no_trade_reason is None  # fresh-day default
