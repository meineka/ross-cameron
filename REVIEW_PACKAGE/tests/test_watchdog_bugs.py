"""Audit-Iter 14 (2026-05-12): watchdog.py restart-logic robustness.

Bugs:
  WD-1 (HIGH): Kein Restart-Loop-Limit. Bot crasht beim Start → watchdog
    restartet alle 5 Min forever. Cloud-Resources gefressen, ggf. Alpaca-
    Rate-Limit-Hit. Jetzt: max 5 Restarts in 1 h → Crashloop-Stop.
  WD-3 (HIGH): is_bot_running returnte False bei wmic-Timeout/Crash → False
    bedeutet "tot" → Restart → 2 Bots parallel. Jetzt: CheckUnknown
    Exception, watchdog skipt den Cycle.
"""
from __future__ import annotations
import sys
import time
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug WD-1: Crashloop-Detection ───────────────────────────────────────────
def test_restart_loop_aborts_after_max_restarts():
    """5 Restarts in 1 h → abort."""
    import watchdog
    now = time.time()
    times = deque([now - 600, now - 500, now - 400, now - 300, now - 100])
    # 5 entries → at cap → abort = True
    assert watchdog._restart_loop_should_abort(times, now=now) is True


def test_restart_loop_continues_under_threshold():
    """4 Restarts in 1 h → continue."""
    import watchdog
    now = time.time()
    times = deque([now - 600, now - 500, now - 400, now - 100])
    assert watchdog._restart_loop_should_abort(times, now=now) is False


def test_restart_loop_window_expires_old_entries():
    """Restarts > 1 h alt zählen nicht mehr."""
    import watchdog
    now = time.time()
    # 5 alte (> 1h) + 1 neuer = total 1 im Window
    times = deque([
        now - 7200, now - 7100, now - 7000, now - 6900, now - 6800,
        now - 60,
    ])
    assert watchdog._restart_loop_should_abort(times, now=now) is False
    # Liste sollte gepruned sein
    assert len(times) == 1


def test_restart_loop_at_exact_max_aborts():
    """Genau MAX_RESTARTS_PER_HOUR → abort."""
    import watchdog
    now = time.time()
    times = deque(now - i * 60 for i in range(watchdog.MAX_RESTARTS_PER_HOUR))
    assert watchdog._restart_loop_should_abort(times, now=now) is True


def test_empty_restart_history_continues():
    import watchdog
    times = deque()
    assert watchdog._restart_loop_should_abort(times, now=time.time()) is False


# ─── Bug WD-3: is_bot_running unklar-Modus ───────────────────────────────────
def test_check_unknown_exception_exists():
    """CheckUnknown muss als Exception-Typ verfügbar sein."""
    import watchdog
    assert issubclass(watchdog.CheckUnknown, Exception)


def test_is_bot_running_raises_check_unknown_on_wmic_failure(monkeypatch):
    """Wenn wmic timeout/crashed → CheckUnknown statt False."""
    import watchdog
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="wmic", timeout=10)

    monkeypatch.setattr(watchdog.subprocess, "check_output", boom)
    with pytest.raises(watchdog.CheckUnknown):
        watchdog.is_bot_running()


def test_is_bot_running_returns_true_when_bot_in_process_list(monkeypatch):
    import watchdog

    fake_output = (
        "Node,CommandLine,ProcessId\r\n"
        "PC,python.exe bot.py --daemon,12345\r\n"
    )
    monkeypatch.setattr(watchdog.subprocess, "check_output",
                        lambda *a, **kw: fake_output)
    running, pids = watchdog.is_bot_running()
    assert running is True
    assert 12345 in pids


def test_is_bot_running_returns_false_when_no_bot():
    import watchdog

    fake_output = (
        "Node,CommandLine,ProcessId\r\n"
        "PC,python.exe other_script.py,99999\r\n"
    )
    import unittest.mock
    with unittest.mock.patch.object(watchdog.subprocess, "check_output",
                                     return_value=fake_output):
        running, pids = watchdog.is_bot_running()
    assert running is False
    assert pids == []
