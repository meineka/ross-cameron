"""process_lock.py — Phase-62 (2026-05-15)

Single-instance enforcement for the live bot.

Problem (raised in ChatGPT reviews 1817 / 1952 / 2012 / 2048): the
StockDataStream singleton enforcement in `alpaca_ws_patch.py` is
per-PROCESS. If two `bot.py` processes start with the same Alpaca
credentials — e.g. operator runs daemon manually while GitHub-Actions
also kicks one off, or watchdog spawns a duplicate before killing the
stale one — the Alpaca account-wide WebSocket connection-limit is
exceeded by parties OUTSIDE the singleton's reach.

Solution: a PID lockfile at bot start. If the file exists AND its PID
is still alive, the second instance refuses to start and exits with
code 75 (EX_TEMPFAIL — try again later). If the PID is stale (process
died, no cleanup) we steal the lock and proceed.

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
LOCKFILE = HERE / "bot.pid"
EXIT_CODE_ALREADY_RUNNING = 75  # EX_TEMPFAIL convention


class AnotherInstanceRunning(RuntimeError):
    """Raised when a live bot is already running with this PID file."""


def _pid_alive(pid: int) -> bool:
    """OS-portable is-process-alive check.

    On Windows os.kill(pid, 0) raises OSError when the process exists,
    PermissionError when we don't have access, and ProcessLookupError
    when it doesn't. We treat permission-denied as "alive but we can't
    signal it" — still counts as a running instance.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, we just can't poke it
    except OSError as e:
        # Windows: errno 22 (EINVAL) is the "alive" signal for kill-0;
        # ESRCH (3) is "no such process".
        if getattr(e, "errno", None) == 3:
            return False
        return True
    return True


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
    """
    existing = _read_lock_pid(lockfile)
    if existing and existing != os.getpid():
        if _pid_alive(existing) and not force:
            raise AnotherInstanceRunning(
                f"another bot instance is running with PID {existing}. "
                f"Lockfile: {lockfile}. Use --force-lock to override "
                f"or wait for the other process to exit."
            )
        # Stale lockfile from a dead process — steal it
        log.info("stealing stale lockfile (prior PID %d not alive)",
                 existing)
    own = os.getpid()
    try:
        lockfile.write_text(str(own), encoding="utf-8")
    except OSError as e:
        raise AnotherInstanceRunning(
            f"could not write lockfile {lockfile}: {e}"
        )
    log.info("acquired process lock PID=%d at %s", own, lockfile)
    return own


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
