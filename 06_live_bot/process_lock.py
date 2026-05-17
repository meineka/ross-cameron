"""process_lock.py — Phase-62 (2026-05-15) + Phase-65 (2026-05-17)

Single-instance enforcement for the live bot AND the data-fetch loop.

Problem (raised in ChatGPT reviews 1817 / 1952 / 2012 / 2048): the
StockDataStream singleton enforcement in `alpaca_ws_patch.py` is
per-PROCESS. If two `bot.py` processes start with the same Alpaca
credentials — e.g. operator runs daemon manually while GitHub-Actions
also kicks one off, or watchdog spawns a duplicate before killing the
stale one — the Alpaca account-wide WebSocket connection-limit is
exceeded by parties OUTSIDE the singleton's reach.

Phase-65 (re-audit after 2026-05-17 reboot incident): the original
Phase-62 implementation had a TOCTOU race — between `_read_lock_pid()`
and `lockfile.write_text()` two processes starting in the same second
could both pass the alive-check, then both overwrite the lock. Real
incident: post-reboot we saw 2× bot.py AND 2× fetch_loop.py both
with timestamps within 4ms.

Fixed by switching to ATOMIC creation via `os.open(..., O_CREAT|O_EXCL)`:
only one process can succeed; the other gets FileExistsError and falls
back to the stale-cleanup path. No race window.

Phase-65 also adds named-lock support so fetch_loop.py can use the
same primitive without colliding with bot.py's `bot.pid`.

Why a lockfile and not just psutil scan:
  - psutil scan needs psutil package (extra dep) and can be racy.
  - A lockfile is dead-simple, OS-portable (no fcntl/msvcrt needed),
    and survives operator restarts cleanly.
  - The is-alive check is OS-native via os.kill(pid, 0).
"""
from __future__ import annotations
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("process-lock")

HERE = Path(__file__).resolve().parent
LOCKFILE = HERE / "bot.pid"             # default lock name — bot.py
FETCH_LOOP_LOCKFILE = HERE / "fetch_loop.pid"  # Phase-65 named lock
SUPERVISOR_LOCKFILE = HERE / "supervisor.pid"  # Phase-67 named lock
EXIT_CODE_ALREADY_RUNNING = 75  # EX_TEMPFAIL convention


class AnotherInstanceRunning(RuntimeError):
    """Raised when a live bot is already running with this PID file."""


def _pid_alive(pid: int) -> bool:
    """OS-portable is-process-alive check.

    Phase-65 (2026-05-17 incident): the previous os.kill(pid, 0) path
    was broken on Windows because signal 0 isn't supported there —
    os.kill raises a `SystemError: <built-in function kill> returned
    a result with an exception set` for alive PIDs. That bubbles all
    the way up and crashes acquire_lock, leaving the lockfile in an
    indeterminate state.

    Fix: on Windows, use ctypes + OpenProcess + GetExitCodeProcess
    (the canonical Win32 alive-check). On POSIX, keep os.kill(0).
    Both paths fail-CLOSED to alive on weird errors so the lockfile
    doesn't get silently stolen from a real owner.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    # POSIX
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as e:
        if getattr(e, "errno", None) == 3:  # ESRCH
            return False
        return True
    return True


_STILL_ACTIVE = 259  # Windows: GetExitCodeProcess returns this for live procs


def _pid_alive_windows(pid: int) -> bool:
    """Win32 alive-check via OpenProcess + GetExitCodeProcess. Returns
    True if the PID belongs to a running process. Defensive: any
    unexpected ctypes error falls back to True (assume alive — better
    to refuse a start than to wrongly steal another instance's lock)."""
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        # ERROR_INVALID_PARAMETER (87) = PID doesn't exist
        # ERROR_ACCESS_DENIED (5) = exists but we can't open; treat as alive
        err = ctypes.get_last_error()
        if err in (5,):  # access denied — process exists
            return True
        return False
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return True  # couldn't read — assume alive (fail-closed)
        return exit_code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _read_lock_pid(lockfile: Path = LOCKFILE) -> int | None:
    """Return the PID stored in lockfile, or None if missing/corrupt."""
    if not lockfile.exists():
        return None
    try:
        text = lockfile.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or not text.lstrip("-").isdigit():
        return None
    return int(text)


def acquire_lock(lockfile: Path = LOCKFILE, *, force: bool = False) -> int:
    """Acquire single-instance lock. Returns own PID on success.

    Raises AnotherInstanceRunning if the lockfile names a still-alive PID
    and `force=False`. With `force=True` the existing lock is stolen
    regardless — use only when the operator explicitly wants this.

    Phase-65: race-safe via O_CREAT|O_EXCL. The OS guarantees only one
    process can win the create call; the loser falls into the stale-
    cleanup branch and decides whether the existing PID is alive.
    """
    own = os.getpid()
    # Fast path: try atomic create. If the file already exists, the
    # OS raises FileExistsError before we even consider stealing.
    try:
        _atomic_create_lock(lockfile, own)
        log.info("acquired process lock PID=%d at %s (atomic-create)",
                 own, lockfile)
        return own
    except FileExistsError:
        pass  # fall through to stale-cleanup logic

    # Lockfile exists — check whether its PID is alive
    existing = _read_lock_pid(lockfile)
    if existing and existing != own and _pid_alive(existing) and not force:
        raise AnotherInstanceRunning(
            f"another bot instance is running with PID {existing}. "
            f"Lockfile: {lockfile}. Use --force-lock to override "
            f"or wait for the other process to exit."
        )
    # Stale or force-stolen — remove + retry atomic create. The retry
    # is still race-safe because O_EXCL again gates the second attempt.
    log.info(
        "stealing lockfile (prior PID=%s alive=%s force=%s)",
        existing, _pid_alive(existing) if existing else "n/a", force,
    )
    try:
        lockfile.unlink()
    except FileNotFoundError:
        pass  # someone else already unlinked — fine
    except OSError as e:
        raise AnotherInstanceRunning(
            f"could not remove stale lockfile {lockfile}: {e}"
        )
    try:
        _atomic_create_lock(lockfile, own)
    except FileExistsError:
        # Lost the race to a fresh competing process after we unlinked.
        raise AnotherInstanceRunning(
            f"lost race to fresh competitor on {lockfile}; refusing "
            f"to start"
        )
    log.info("acquired process lock PID=%d at %s (after stale-cleanup)",
             own, lockfile)
    return own


def _atomic_create_lock(lockfile: Path, pid: int) -> None:
    """Create lockfile with exclusive semantics. Raises FileExistsError
    if it already exists. This is the race-free primitive — only ONE
    competing process can succeed even if both call this nanoseconds
    apart, because O_EXCL is checked atomically by the OS."""
    fd = os.open(
        str(lockfile),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        os.write(fd, str(pid).encode("utf-8"))
    finally:
        os.close(fd)


def release_lock(lockfile: Path = LOCKFILE) -> bool:
    """Release the lock if we own it. Returns True if we deleted the
    file, False if it didn't exist or wasn't ours."""
    existing = _read_lock_pid(lockfile)
    if existing != os.getpid():
        # Someone else owns it (or it's already gone) — don't touch.
        return False
    try:
        lockfile.unlink()
        log.info("released process lock at %s", lockfile)
        return True
    except OSError as e:
        log.warning("failed to remove lockfile %s: %s", lockfile, e)
        return False


def enforce_single_instance_or_exit(lockfile: Path = LOCKFILE, *,
                                       force: bool = False) -> int:
    """Convenience wrapper for bot.main(): try to acquire lock, exit
    with EX_TEMPFAIL=75 if another instance is alive. Returns our PID
    on success."""
    try:
        return acquire_lock(lockfile, force=force)
    except AnotherInstanceRunning as e:
        log.error("REFUSING TO START: %s", e)
        # Also write to stderr so the operator running from CLI sees it
        print(f"[FATAL] {e}", file=sys.stderr)
        sys.exit(EXIT_CODE_ALREADY_RUNNING)


def enforce_single_fetch_loop_or_exit(*, force: bool = False) -> int:
    """Phase-65: same single-instance guarantee for the fetch_loop. Uses
    a SEPARATE lockfile from bot.py so the two can coexist legitimately.
    Real incident motivating this: post-reboot we saw 2× fetch_loop.py
    racing on the same parquet write, corrupting the dataset."""
    return enforce_single_instance_or_exit(FETCH_LOOP_LOCKFILE, force=force)


def release_fetch_loop_lock() -> bool:
    """Companion release for the fetch_loop named lock."""
    return release_lock(FETCH_LOOP_LOCKFILE)


def enforce_single_supervisor_or_exit(*, force: bool = False) -> int:
    """Phase-67: single-instance enforcement for supervisor.py.
    The supervisor is itself the auto-corrector — having two would
    cause them to "fight" over duplicate detection."""
    return enforce_single_instance_or_exit(SUPERVISOR_LOCKFILE, force=force)


def release_supervisor_lock() -> bool:
    return release_lock(SUPERVISOR_LOCKFILE)
