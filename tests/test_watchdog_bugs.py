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


# ─── Phase-12 (ChatGPT-19:05 P0.1): bot-Python resolution + dep-preflight ───

def test_resolve_bot_python_prefers_env_var(monkeypatch, tmp_path):
    """BOT_PYTHON env var wins over .venv / sys.executable."""
    import watchdog
    fake_py = tmp_path / "custom_python.exe"
    fake_py.write_text("")
    monkeypatch.setenv("BOT_PYTHON", str(fake_py))
    assert watchdog.resolve_bot_python() == str(fake_py)


def test_resolve_bot_python_ignores_env_var_when_path_missing(monkeypatch):
    """A BOT_PYTHON pointing at a non-existent file falls through."""
    import watchdog
    monkeypatch.setenv("BOT_PYTHON", "C:\\does-not-exist-xyz.exe")
    # Should fall through to .venv or sys.executable, never the bad path
    result = watchdog.resolve_bot_python()
    assert result != "C:\\does-not-exist-xyz.exe"


def test_resolve_bot_python_falls_back_to_sys_executable(monkeypatch):
    """No BOT_PYTHON, no .venv → sys.executable."""
    import watchdog, sys as _sys
    monkeypatch.delenv("BOT_PYTHON", raising=False)
    # Point HERE/REPO_ROOT at empty dirs so .venv lookups miss
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(watchdog, "HERE", pathlib.Path(tmp))
        monkeypatch.setattr(watchdog, "REPO_ROOT", pathlib.Path(tmp))
        assert watchdog.resolve_bot_python() == _sys.executable


def test_preflight_dependencies_ok_with_current_python():
    """Probing the test interpreter must succeed for at least pandas/pyarrow
    (they're test deps). Use a tighter dep set to keep the assertion stable
    even if alpaca/yfinance aren't installed in the test env."""
    import watchdog, sys as _sys
    # The test runner clearly has pandas (pytest collects parquet tests)
    ok, missing = watchdog.preflight_dependencies(_sys.executable, deps=("pandas",))
    assert ok is True
    assert missing == []


def test_preflight_dependencies_missing_returns_named_module():
    """Asking for a non-existent module returns ok=False AND names it."""
    import watchdog, sys as _sys
    ok, missing = watchdog.preflight_dependencies(
        _sys.executable, deps=("definitely_not_installed_xyz_module",))
    assert ok is False
    assert "definitely_not_installed_xyz_module" in missing


def test_preflight_handles_bad_interpreter_path():
    """A non-existent interpreter returns ok=False without crashing."""
    import watchdog
    ok, missing = watchdog.preflight_dependencies("C:\\not-a-real-python.exe",
                                                    deps=("alpaca",))
    assert ok is False
    # All deps reported missing when the probe itself failed
    assert "alpaca" in missing


def test_start_bot_uses_resolved_bot_python(monkeypatch, tmp_path):
    """start_bot() must pass the resolved bot_python (not sys.executable)
    as argv[0] of the spawned process. Critical for venv parity."""
    import watchdog, subprocess as _sp
    fake_py = str(tmp_path / "bot_python.exe")
    monkeypatch.setattr(watchdog, "resolve_bot_python", lambda: fake_py)
    # Stub secrets + position-check so start_bot reaches the Popen call
    import sys as _sys
    _sys.path.insert(0, str(watchdog.HERE))
    import types
    fake_secrets = types.SimpleNamespace(get_alpaca_keys=lambda: ("K", "S"))
    monkeypatch.setitem(_sys.modules, "secrets_loader", fake_secrets)
    monkeypatch.setattr(watchdog, "_position_check_via_bot_python",
                         lambda py, k, s: (True, 0))
    seen = {}
    class FakeProc:
        pid = 4321
    def fake_popen(argv, **kw):
        seen["argv"] = argv
        return FakeProc()
    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)
    # Ensure daemon.log open() works in tmp dir
    monkeypatch.setattr(watchdog, "HERE", tmp_path)
    pid = watchdog.start_bot()
    assert pid == 4321
    assert seen["argv"][0] == fake_py, \
        f"start_bot must use resolved bot-Python, got argv[0]={seen['argv'][0]}"
    assert seen["argv"][1:] == ["bot.py", "--daemon"]


def test_position_check_runs_in_subprocess_not_watchdog(monkeypatch, tmp_path):
    """The position-check must call out to bot-Python, not import alpaca
    in the watchdog's own process. This is the core P0.1 fix."""
    import watchdog
    seen = {}
    def fake_run(argv, **kw):
        seen["argv"] = argv
        class R:
            returncode = 0
            stdout = '{"n": 0, "symbols": []}\n'
            stderr = ""
        return R()
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    fake_py = str(tmp_path / "bot_python.exe")
    ok, n = watchdog._position_check_via_bot_python(fake_py, "K", "S")
    assert ok is True
    assert n == 0
    # Confirm the subprocess was invoked with bot-Python, not sys.executable
    assert seen["argv"][0] == fake_py


def test_position_check_failure_returns_unknown(monkeypatch, tmp_path):
    """Subprocess failure (e.g. missing alpaca) returns ok=False so caller
    will NOT restart blindly. Mirrors CheckUnknown semantics."""
    import watchdog
    def fake_run(argv, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "ModuleNotFoundError: No module named 'alpaca'"
        return R()
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)
    ok, n = watchdog._position_check_via_bot_python(
        str(tmp_path / "x.exe"), "K", "S")
    assert ok is False
    assert n == -1


def test_main_exits_on_dep_preflight_failure(monkeypatch):
    """When preflight reports missing deps, main() must return cleanly
    (no restart-spam loop). This is the operator-facing fix for
    'No module named alpaca' every 5 minutes."""
    import watchdog
    monkeypatch.setattr(watchdog, "resolve_bot_python", lambda: "fake.exe")
    monkeypatch.setattr(watchdog, "preflight_dependencies",
                         lambda py, deps=watchdog.REQUIRED_DEPS: (False, ["alpaca"]))
    # If main() falls through to the while-True we never return.
    # Add a timeout via a sentinel: patch is_bot_running to raise so we'd
    # know if we got there.
    def must_not_be_called():
        raise AssertionError("main() reached the restart loop despite missing deps")
    monkeypatch.setattr(watchdog, "is_bot_running", must_not_be_called)
    # Should return cleanly
    watchdog.main()
