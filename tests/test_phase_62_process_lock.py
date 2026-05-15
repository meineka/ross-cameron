"""Phase-62: single-instance enforcement via PID lockfile.

Raised in ChatGPT reviews 1817/1952/2012/2048: "StockDataStream
singleton is per-process; if a second bot.py starts with the same
Alpaca credentials, the account-wide WS connection limit can still
be tripped". The lockfile blocks that scenario.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture
def lockfile(tmp_path):
    return tmp_path / "bot.pid"


def test_acquire_writes_own_pid(lockfile):
    """Fresh acquire writes os.getpid() into the lockfile."""
    from process_lock import acquire_lock
    pid = acquire_lock(lockfile)
    assert pid == os.getpid()
    assert lockfile.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_release_removes_own_lockfile(lockfile):
    from process_lock import acquire_lock, release_lock
    acquire_lock(lockfile)
    assert release_lock(lockfile) is True
    assert not lockfile.exists()


def test_release_does_nothing_when_not_ours(lockfile):
    """Lockfile owned by another PID — release_lock must NOT delete it."""
    from process_lock import release_lock
    lockfile.write_text("999999", encoding="utf-8")
    assert release_lock(lockfile) is False
    assert lockfile.exists()  # untouched


def test_second_acquire_raises_when_other_pid_alive(lockfile, monkeypatch):
    """The headline regression: PID 999999 (we pretend it's alive) must
    block a fresh acquire."""
    import process_lock
    lockfile.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    with pytest.raises(process_lock.AnotherInstanceRunning,
                        match="999999"):
        process_lock.acquire_lock(lockfile)


def test_stale_lockfile_is_stolen(lockfile, monkeypatch):
    """If the lockfile's PID is no longer alive, the new bot takes
    over silently. Avoids requiring operator cleanup after a crash."""
    import process_lock
    lockfile.write_text("888888", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: False)
    pid = process_lock.acquire_lock(lockfile)
    assert pid == os.getpid()
    assert lockfile.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_force_steal_overrides_alive_check(lockfile, monkeypatch):
    """Operator-override: --force-lock takes the lock even from an
    alive prior PID. Use case: bot stuck in unkillable state."""
    import process_lock
    lockfile.write_text("777777", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    pid = process_lock.acquire_lock(lockfile, force=True)
    assert pid == os.getpid()


def test_corrupt_lockfile_treated_as_missing(lockfile):
    """Lockfile with garbage content (e.g. half-written, hex string)
    should not crash — treat as no-lock."""
    from process_lock import acquire_lock
    lockfile.write_text("not-a-pid", encoding="utf-8")
    pid = acquire_lock(lockfile)
    assert pid == os.getpid()


def test_enforce_single_instance_exits_with_75_on_conflict(lockfile,
                                                              monkeypatch):
    """Bot-startup integration: enforce_single_instance_or_exit must
    exit with code 75 (EX_TEMPFAIL) when blocked."""
    import process_lock
    lockfile.write_text("666666", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    with pytest.raises(SystemExit) as exc:
        process_lock.enforce_single_instance_or_exit(lockfile)
    assert exc.value.code == process_lock.EXIT_CODE_ALREADY_RUNNING == 75


def test_pid_alive_returns_true_for_own_process():
    """Sanity: our own PID is alive."""
    from process_lock import _pid_alive
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_returns_false_for_zero_or_negative():
    from process_lock import _pid_alive
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


def test_bot_main_wires_in_lock():
    """Source-grep: bot.main() must call enforce_single_instance_or_exit
    BEFORE starting the trade loop. If a future refactor removes this
    line, the test catches it."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from process_lock import" in src
    assert "enforce_single_instance_or_exit" in src
    assert "atexit.register(release_lock)" in src
