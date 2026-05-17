"""Phase-65: race-safe atomic lockfile.

Real-world incident 2026-05-17: post-reboot, two bot.py and two
fetch_loop.py processes were observed running simultaneously despite
Phase-62's PID lockfile. Root cause: TOCTOU race between
_read_lock_pid() and lockfile.write_text() — both competitors could
pass the "no alive PID" check before either wrote the lock.

Fix: switch to atomic O_CREAT|O_EXCL semantics. Only ONE process can
win the create call; the loser falls into stale-cleanup. The OS
guarantees this is race-free.

This test file pins down:
  - Atomic create succeeds when lockfile doesn't exist
  - Atomic create raises FileExistsError when it does
  - Concurrent acquire from threads → exactly one winner
  - Fetch-loop lockfile uses a separate file from bot.py lockfile
  - --force-lock works through the new code path
"""
from __future__ import annotations
import os
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. Atomic create primitive ──────────────────────────────────────────

def test_atomic_create_writes_pid_to_fresh_lockfile(tmp_path):
    from process_lock import _atomic_create_lock
    lockfile = tmp_path / "test.pid"
    _atomic_create_lock(lockfile, 12345)
    assert lockfile.read_text(encoding="utf-8") == "12345"


def test_atomic_create_raises_when_lockfile_exists(tmp_path):
    """Headline fix: second caller MUST get FileExistsError, not
    silently overwrite (which was the Phase-62 TOCTOU bug)."""
    from process_lock import _atomic_create_lock
    lockfile = tmp_path / "test.pid"
    lockfile.write_text("99999", encoding="utf-8")
    with pytest.raises(FileExistsError):
        _atomic_create_lock(lockfile, 12345)
    # Original content untouched — atomicity preserved
    assert lockfile.read_text(encoding="utf-8") == "99999"


# ─── 2. acquire_lock uses the atomic primitive ───────────────────────────

def test_acquire_lock_uses_atomic_create_fresh(tmp_path):
    from process_lock import acquire_lock
    lockfile = tmp_path / "test.pid"
    pid = acquire_lock(lockfile)
    assert pid == os.getpid()
    assert lockfile.read_text(encoding="utf-8") == str(os.getpid())


def test_acquire_lock_steals_stale_via_unlink_then_atomic_retry(
        tmp_path, monkeypatch):
    """A stale PID (dead process) — the atomic-create raises FileExists,
    fallback path unlinks and retries via atomic create. Net result:
    we own the lock."""
    import process_lock
    lockfile = tmp_path / "test.pid"
    lockfile.write_text("888888", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: False)
    pid = process_lock.acquire_lock(lockfile)
    assert pid == os.getpid()
    assert lockfile.read_text(encoding="utf-8") == str(os.getpid())


def test_acquire_lock_refuses_alive_competitor(tmp_path, monkeypatch):
    import process_lock
    lockfile = tmp_path / "test.pid"
    lockfile.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    with pytest.raises(process_lock.AnotherInstanceRunning,
                        match="999999"):
        process_lock.acquire_lock(lockfile)


def test_acquire_lock_force_steals_even_alive(tmp_path, monkeypatch):
    import process_lock
    lockfile = tmp_path / "test.pid"
    lockfile.write_text("777777", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    pid = process_lock.acquire_lock(lockfile, force=True)
    assert pid == os.getpid()


# ─── 3. Concurrent acquire from threads — race regression ──────────────

def test_concurrent_acquire_yields_exactly_one_winner(tmp_path,
                                                         monkeypatch):
    """The real Phase-65 regression test: 20 simulated-different-processes
    race for the same lockfile. Before the O_EXCL fix, two-or-more
    could all pass the alive check and overwrite each other. Now:
    exactly one succeeds.

    Implementation detail: real threads share os.getpid(), so to simulate
    different processes we give each thread a unique fake PID via a
    thread-local + monkeypatched os.getpid. Combined with stub
    _pid_alive=True, failed acquirers cannot escape via stale-steal."""
    import process_lock
    import os as _os
    lockfile = tmp_path / "race.pid"

    # Thread-local fake PID so each "process" looks distinct
    fake_pid_local = threading.local()
    pid_counter = [1000]
    pid_lock = threading.Lock()

    def _next_pid():
        with pid_lock:
            pid_counter[0] += 1
            return pid_counter[0]

    def _fake_getpid():
        if not hasattr(fake_pid_local, "pid"):
            fake_pid_local.pid = _next_pid()
        return fake_pid_local.pid

    monkeypatch.setattr(_os, "getpid", _fake_getpid)
    monkeypatch.setattr(process_lock.os, "getpid", _fake_getpid)
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)

    winners: list[int] = []
    failures: list[Exception] = []
    barrier = threading.Barrier(20)

    def _worker():
        barrier.wait()  # release all threads at once
        try:
            process_lock.acquire_lock(lockfile)
            winners.append(threading.get_ident())
        except process_lock.AnotherInstanceRunning as e:
            failures.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(winners) == 1, (
        f"RACE BUG: {len(winners)} winners (expected exactly 1). "
        f"Lockfile content: {lockfile.read_text(encoding='utf-8')}"
    )
    assert len(failures) == 19, (
        f"expected 19 losers, got {len(failures)}"
    )


# ─── 4. Fetch-loop named lock vs bot lock ───────────────────────────────

def test_fetch_loop_uses_separate_lockfile_from_bot(tmp_path,
                                                       monkeypatch):
    """Phase-65: bot.py and fetch_loop.py can BOTH run as singletons
    without colliding because they use different lockfile names.
    Verifies the named-lock convenience wrappers point at different
    files."""
    import process_lock
    monkeypatch.setattr(process_lock, "LOCKFILE", tmp_path / "bot.pid")
    monkeypatch.setattr(process_lock, "FETCH_LOOP_LOCKFILE",
                          tmp_path / "fetch_loop.pid")
    # Acquire bot lock
    process_lock.acquire_lock(process_lock.LOCKFILE)
    # Acquire fetch-loop lock — must NOT collide
    process_lock.acquire_lock(process_lock.FETCH_LOOP_LOCKFILE)
    assert process_lock.LOCKFILE.exists()
    assert process_lock.FETCH_LOOP_LOCKFILE.exists()
    # Different content (each holds own PID), but in this test they're
    # the same because the test process is the same PID. The point is
    # both files exist + were acquired without raising.


def test_release_fetch_loop_lock_removes_only_fetch_loop_file(
        tmp_path, monkeypatch):
    import process_lock
    bot_lock = tmp_path / "bot.pid"
    loop_lock = tmp_path / "fetch_loop.pid"
    monkeypatch.setattr(process_lock, "LOCKFILE", bot_lock)
    monkeypatch.setattr(process_lock, "FETCH_LOOP_LOCKFILE", loop_lock)
    process_lock.acquire_lock(bot_lock)
    process_lock.acquire_lock(loop_lock)
    assert process_lock.release_fetch_loop_lock() is True
    assert not loop_lock.exists()
    assert bot_lock.exists()  # bot lock untouched


# ─── 5. fetch_loop.main() wiring ────────────────────────────────────────

def test_fetch_loop_main_imports_lock_helpers():
    """Source-grep: if a future refactor accidentally removes the
    lockfile wiring from fetch_loop.main(), this test catches it."""
    src = (ROOT / "06_live_bot" / "fetch_loop.py").read_text(
        encoding="utf-8"
    )
    assert "from process_lock import" in src
    assert "enforce_single_fetch_loop_or_exit" in src
    assert "release_fetch_loop_lock" in src
    assert "atexit.register(release_fetch_loop_lock)" in src


def test_enforce_single_fetch_loop_exits_75_on_alive_competitor(
        tmp_path, monkeypatch):
    import process_lock
    monkeypatch.setattr(process_lock, "FETCH_LOOP_LOCKFILE",
                          tmp_path / "fetch_loop.pid")
    (tmp_path / "fetch_loop.pid").write_text("55555", encoding="utf-8")
    monkeypatch.setattr(process_lock, "_pid_alive", lambda pid: True)
    with pytest.raises(SystemExit) as exc:
        process_lock.enforce_single_fetch_loop_or_exit()
    assert exc.value.code == 75


# ─── 6. Hardening: corrupt lockfile + retry ─────────────────────────────

def test_acquire_lock_handles_corrupt_existing_lockfile(tmp_path):
    """Garbage-content lockfile (half-write from a crashed process)
    should be cleaned up and re-acquired."""
    from process_lock import acquire_lock
    lockfile = tmp_path / "corrupt.pid"
    lockfile.write_text("not-a-number", encoding="utf-8")
    pid = acquire_lock(lockfile)
    assert pid == os.getpid()
    assert lockfile.read_text(encoding="utf-8") == str(os.getpid())
