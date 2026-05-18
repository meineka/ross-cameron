"""Phase-74: PowerShell-native healthcheck (30-min scheduled task).

User: "jede halbe stunde die existenz des live trading bots prüfen
und bei Fehlern wiederherstellen, alles im powershell"

The healthcheck.ps1 script is the LAST-RESORT backstop — runs from
Windows Task Scheduler so it survives even if the Python supervisor
itself crashes. Source-grep tests pin the contract so the next refactor
can't silently remove the auto-spawn logic.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "06_live_bot" / "healthcheck.ps1"


def _src() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# ─── 1. Script exists + structure ────────────────────────────────────────

def test_healthcheck_script_exists():
    assert SCRIPT.exists(), f"missing: {SCRIPT}"


def test_healthcheck_uses_native_powershell():
    """No Python invocations in the check itself — pure PowerShell."""
    src = _src()
    # Spawning daemons uses python.exe — that's allowed.
    # But the CHECK logic must not depend on a running Python interpreter.
    assert "Get-WmiObject" in src or "Get-CimInstance" in src
    assert "Get-Process" in src


def test_healthcheck_writes_log():
    """Every cycle must log to healthcheck.log so the operator has a
    trail without checking Task Scheduler history."""
    src = _src()
    assert "healthcheck.log" in src
    assert "Add-Content" in src or "Out-File" in src


# ─── 2. Checks all 4 daemons ─────────────────────────────────────────────

def test_healthcheck_checks_bot_py_daemon():
    src = _src()
    assert "bot.py.*--daemon" in src or "bot\\.py.*--daemon" in src


def test_healthcheck_checks_watchdog():
    assert "watchdog" in _src()


def test_healthcheck_checks_fetch_loop():
    assert "fetch_loop" in _src()


def test_healthcheck_checks_supervisor():
    assert "supervisor" in _src()


def test_healthcheck_checks_daemon_log_freshness():
    src = _src()
    assert "daemon.log" in src
    # Must check mtime (LastWriteTime)
    assert "LastWriteTime" in src


# ─── 3. Auto-repair on dead daemons ──────────────────────────────────────

def test_healthcheck_spawns_dead_watchdog():
    """If watchdog is dead, healthcheck spawns it (watchdog in turn
    spawns bot)."""
    src = _src()
    # Must reference Start-Daemon or Start-Process for watchdog
    assert "Start-Daemon" in src or ("watchdog.py" in src and
                                       "Start-Process" in src)


def test_healthcheck_spawns_dead_fetch_loop():
    src = _src()
    assert "fetch_loop.py" in src and (
        "Start-Daemon" in src or "Start-Process" in src
    )


def test_healthcheck_spawns_dead_supervisor():
    src = _src()
    assert "supervisor.py" in src and (
        "Start-Daemon" in src or "Start-Process" in src
    )


# ─── 4. Stale-lockfile cleanup ──────────────────────────────────────────

def test_healthcheck_removes_stale_lockfiles():
    """Phase-65 atomic-lockfile only blocks NEW spawns; stale lockfiles
    from crashed processes still block restarts. healthcheck must
    cleanup."""
    src = _src()
    assert "Remove-StaleLockfile" in src or "Test-Lockfile" in src
    # Must touch the three lockfile names
    assert "bot.pid" in src
    assert "fetch_loop.pid" in src
    assert "supervisor.pid" in src


def test_healthcheck_lockfile_detects_dead_pid():
    """The stale-detection must check if PID is actually alive — not
    just unlink based on file presence."""
    src = _src()
    # Must look up the process by PID after reading lockfile
    assert "Get-Process -Id" in src


# ─── 5. Env-vars propagated to spawned daemons ──────────────────────────

def test_healthcheck_propagates_env_to_spawns():
    """STRATEGY_VARIANT, SKIP_HARD_FLAT_TODAY etc must be loaded from
    06_live_bot/.env before spawning daemons. Otherwise auto-respawn
    after crash silently reverts to strict default."""
    src = _src()
    assert ".env" in src
    assert "SetEnvironmentVariable" in src or "$env:" in src


# ─── 6. Safety / dry-run mode ───────────────────────────────────────────

def test_healthcheck_supports_dryrun():
    """Operator should be able to see what would happen without action."""
    src = _src()
    assert "DryRun" in src
    assert "[switch]$DryRun" in src or "[switch]" in src


def test_healthcheck_always_exits_zero():
    """A failed health check should NOT mark the scheduled task as
    failed — that triggers Windows Event Log noise. The script logs
    failures internally and exits 0."""
    src = _src()
    assert "exit 0" in src


# ─── 7. Documentation / discoverability ─────────────────────────────────

def test_healthcheck_has_header_comment():
    """Future operator should understand what this does at a glance."""
    src = _src()
    assert "Phase-74" in src
    assert "30 min" in src.lower() or "every 30" in src.lower()


def test_healthcheck_documents_difference_from_supervisor():
    """Both this and Phase-67 supervisor.py do similar things — the
    script must explain when which fires."""
    src = _src()
    # Mentions Python supervisor and explains layering
    assert "supervisor" in src.lower()
    assert "last-resort" in src.lower() or "backstop" in src.lower()
