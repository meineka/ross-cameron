"""Audit-Iter 24 (2026-05-12): ReconnectBackoff edge cases.

Bug RB-7 (MED): pathological inputs (negative base/cap, max_fails<0) wurden
  silent akzeptiert. Negative base → negative delay → asyncio.sleep behaviors
  undefined oder ValueError.
  Fix: __init__ validation raises ValueError.

Bug RB-9 (MED): Multiple Bot-Instances könnten gleichzeitig reconnecten
  (thundering-herd) und Alpaca-Server gleichzeitig hitten.
  Fix: optional jitter (±10% default-off).

Plus comprehensive Edge-Cases die in test_improvements.py nicht abgedeckt
waren: cap-clamping, reset-after-breaker, partial-recovery-sequences.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug RB-7: Input validation ──────────────────────────────────────────────
def test_negative_base_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="base_sec"):
        ReconnectBackoff(base_sec=-1.0)


def test_zero_base_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="base_sec"):
        ReconnectBackoff(base_sec=0.0)


def test_cap_below_base_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="cap_sec"):
        ReconnectBackoff(base_sec=10.0, cap_sec=5.0)


def test_negative_max_fails_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="max_consec_fails"):
        ReconnectBackoff(max_consec_fails=-1)


def test_jitter_above_one_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="jitter"):
        ReconnectBackoff(jitter=1.5)


def test_negative_jitter_raises():
    from reconnect_backoff import ReconnectBackoff
    with pytest.raises(ValueError, match="jitter"):
        ReconnectBackoff(jitter=-0.1)


# ─── Bug RB-9: Jitter behavior ───────────────────────────────────────────────
def test_jitter_default_off_returns_exact_delay():
    """Default-no-jitter → deterministic delays."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=60.0)
    assert b.fail() == 1.0
    assert b.fail() == 2.0
    assert b.fail() == 4.0


def test_jitter_applied_when_enabled():
    """jitter=0.1 → delay variiert in [0.9 * base, 1.1 * base]."""
    from reconnect_backoff import ReconnectBackoff
    import random
    random.seed(42)
    b = ReconnectBackoff(base_sec=10.0, cap_sec=60.0, jitter=0.1)
    delay = b.fail()
    assert 9.0 <= delay <= 11.0
    assert delay != 10.0  # very high probability of jitter applying


def test_jitter_never_negative():
    """Sanity: jitter*delay darf delay nicht negativ machen."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=2.0, jitter=1.0, max_consec_fails=10)
    for _ in range(5):
        d = b.fail()
        assert d >= 0


# ─── Exponential growth + cap ────────────────────────────────────────────────
def test_exponential_growth():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=60.0, max_consec_fails=8)
    assert b.fail() == 1.0   # 2^0
    assert b.fail() == 2.0   # 2^1
    assert b.fail() == 4.0   # 2^2
    assert b.fail() == 8.0   # 2^3
    assert b.fail() == 16.0  # 2^4
    assert b.fail() == 32.0  # 2^5


def test_cap_clamps_high_delays():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=10.0, max_consec_fails=20)
    delays = [b.fail() for _ in range(10)]
    # alle delays nach 4. fail sollten cap=10 sein
    assert delays[-1] == 10.0
    assert delays[-2] == 10.0
    assert delays[-3] == 10.0


# ─── Circuit-Breaker semantics ───────────────────────────────────────────────
def test_circuit_breaker_trips_after_max():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=3)
    b.fail(); b.fail(); b.fail()  # 3 OK
    with pytest.raises(RuntimeError, match="Circuit-Breaker"):
        b.fail()


def test_reset_after_breaker_allows_new_attempts():
    """Nach Breaker + reset → wieder N attempts möglich."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=2)
    b.fail(); b.fail()
    with pytest.raises(RuntimeError):
        b.fail()
    b.reset()
    # Jetzt wieder 2 attempts:
    assert b.fail() == 1.0
    assert b.fail() == 2.0


def test_zero_max_fails_trips_first_fail():
    """max=0: erster fail() raised — backoff effektiv disabled."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=0)
    with pytest.raises(RuntimeError):
        b.fail()


# ─── Recovery sequences ──────────────────────────────────────────────────────
def test_reset_after_success_resets_counter():
    """3 fails, reset, 1 fail → back to 2^0 = 1s."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff()
    b.fail(); b.fail(); b.fail()
    assert b.consec_fails == 3
    b.reset()
    assert b.consec_fails == 0
    assert b.fail() == 1.0  # base again


def test_multiple_resets_idempotent():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff()
    b.reset(); b.reset(); b.reset()
    assert b.consec_fails == 0


# ─── async sleep_after_fail ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_async_sleep_completes(monkeypatch):
    """sleep_after_fail wartet wirklich (mocked)."""
    from reconnect_backoff import ReconnectBackoff
    import asyncio
    sleeps = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    b = ReconnectBackoff(base_sec=1.0)
    await b.sleep_after_fail()
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_async_sleep_propagates_circuit_breaker():
    """sleep_after_fail propagiert die RuntimeError beim breaker."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=1)
    await b.sleep_after_fail()  # 1st OK
    with pytest.raises(RuntimeError):
        await b.sleep_after_fail()
