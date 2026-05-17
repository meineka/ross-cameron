"""Phase-67: 30-min auto-correcting supervisor.

User: "gut hast du einen loop der 2 mal pro stunde fehler verbessert"

The supervisor is a META-watchdog that runs every 30 min (2x per hour),
detects and (optionally) auto-corrects:
  - Duplicate logical processes
  - Stale lockfiles (PID dead)
  - Dead components (fetch_loop, watchdog)
  - Lockfile/process PID mismatch

Conservative-by-default: dry-run unless --auto passed. NEVER kills bot.py
that may have open positions — alerts operator instead.

These tests pin the audit invariants and reconciler actions.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. Role classification ──────────────────────────────────────────────

@pytest.mark.parametrize("cmdline,expected_role", [
    (".venv/Scripts/python.exe supervisor.py", "supervisor"),
    ("python  watchdog.py", "watchdog"),
    ("python.exe 06_live_bot/fetch_loop.py", "fetch_loop"),
    ("python.exe /path/fetch_historical_range.py --start ...", "fetch_hist"),
    (".venv/python.exe bot.py --daemon", "bot"),
    ("python bot.py --check-connection", "other"),  # not the daemon
    ("python.exe some_other_script.py", "other"),
])
def test_classify_role(cmdline, expected_role):
    from supervisor import _classify_role
    assert _classify_role(cmdline) == expected_role


# ─── 2. Logical-process grouping (venv-launcher pairing) ────────────────

def test_group_pairs_venv_launcher_with_interpreter():
    """The headline win: Windows venv-launcher + system-py interpreter
    are ONE logical process, not two duplicates."""
    from supervisor import ProcInfo, group_into_logical
    procs = [
        # bash → venv-launcher (PID 100) → system-py interpreter (PID 200)
        ProcInfo(pid=100, parent_pid=10, role="bot",
                  is_venv_launcher=True,
                  cmdline=".venv/python.exe bot.py --daemon"),
        ProcInfo(pid=200, parent_pid=100, role="bot",
                  is_venv_launcher=False,
                  cmdline="system-python.exe bot.py --daemon"),
    ]
    logical = group_into_logical(procs)
    assert len(logical) == 1
    assert logical[0].role == "bot"
    assert logical[0].interpreter_pid == 200
    assert logical[0].launcher_pid == 100


def test_group_does_not_pair_different_roles():
    """A bot launcher must not match a fetch_loop interpreter even if
    PIDs/parent-PIDs happen to align."""
    from supervisor import ProcInfo, group_into_logical
    procs = [
        ProcInfo(pid=100, parent_pid=10, role="bot",
                  is_venv_launcher=True, cmdline=".venv/python bot.py"),
        ProcInfo(pid=200, parent_pid=100, role="fetch_loop",
                  is_venv_launcher=False, cmdline="python fetch_loop.py"),
    ]
    logical = group_into_logical(procs)
    # bot launcher is orphaned (no matching interpreter), fetch_loop solo
    roles = [lp.role for lp in logical]
    assert "fetch_loop" in roles
    # bot launcher counts as its own (orphan)
    assert "bot" in roles


def test_group_two_real_logical_bots_detected_as_duplicates():
    """Different PARENTS, different launcher chains → 2 logical bots."""
    from supervisor import ProcInfo, group_into_logical
    procs = [
        # bot instance #1: parent 10
        ProcInfo(pid=100, parent_pid=10, role="bot",
                  is_venv_launcher=True, cmdline=".venv/python bot.py --daemon"),
        ProcInfo(pid=200, parent_pid=100, role="bot",
                  is_venv_launcher=False, cmdline="python bot.py --daemon"),
        # bot instance #2: parent 20
        ProcInfo(pid=300, parent_pid=20, role="bot",
                  is_venv_launcher=True, cmdline=".venv/python bot.py --daemon"),
        ProcInfo(pid=400, parent_pid=300, role="bot",
                  is_venv_launcher=False, cmdline="python bot.py --daemon"),
    ]
    logical = group_into_logical(procs)
    bot_logical = [lp for lp in logical if lp.role == "bot"]
    assert len(bot_logical) == 2, f"expected 2 logical bots, got {len(bot_logical)}"


def test_group_filters_out_other_role():
    """Random non-cameron python processes don't pollute the inventory."""
    from supervisor import ProcInfo, group_into_logical
    procs = [
        ProcInfo(pid=100, parent_pid=10, role="other",
                  is_venv_launcher=False, cmdline="python random_script.py"),
    ]
    assert group_into_logical(procs) == []


# ─── 3. Audit invariants ────────────────────────────────────────────────

def _lp(role, pid, launcher=None):
    from supervisor import LogicalProc
    return LogicalProc(role=role, interpreter_pid=pid, launcher_pid=launcher)


def test_audit_detects_no_watchdog(monkeypatch):
    import supervisor
    issues = supervisor.audit([_lp("bot", 100), _lp("fetch_loop", 200)])
    types = [i.type for i in issues]
    assert "watchdog_dead" in types
    wd = next(i for i in issues if i.type == "watchdog_dead")
    assert wd.severity == "error"


def test_audit_detects_no_bot(monkeypatch, tmp_path):
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    issues = supervisor.audit([_lp("watchdog", 100),
                                  _lp("fetch_loop", 200)])
    types = [i.type for i in issues]
    assert "bot_dead" in types


def test_audit_detects_duplicate_bot(monkeypatch, tmp_path):
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    issues = supervisor.audit([
        _lp("watchdog", 100), _lp("bot", 200), _lp("bot", 300),
        _lp("fetch_loop", 400),
    ])
    bot_issues = [i for i in issues if i.type == "bot_duplicate"]
    assert len(bot_issues) == 1
    assert bot_issues[0].severity == "error"


def test_audit_detects_stale_bot_pid(monkeypatch, tmp_path):
    """bot.pid file exists but no bot process running."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    (tmp_path / "bot.pid").write_text("99999", encoding="utf-8")
    issues = supervisor.audit([_lp("watchdog", 100),
                                  _lp("fetch_loop", 200)])
    types = [i.type for i in issues]
    assert "bot_pid_stale" in types


def test_audit_detects_pid_mismatch(monkeypatch, tmp_path):
    """bot.pid says one PID but bot.py is running under another."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    (tmp_path / "bot.pid").write_text("99999", encoding="utf-8")
    issues = supervisor.audit([_lp("watchdog", 100), _lp("bot", 200),
                                  _lp("fetch_loop", 300)])
    types = [i.type for i in issues]
    assert "bot_pid_mismatch" in types


def test_audit_passes_clean_state(monkeypatch, tmp_path):
    """1 watchdog + 1 bot (PID matches lockfile) + 1 fetch_loop
    (PID matches lockfile) → ZERO error/warn issues."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    (tmp_path / "bot.pid").write_text("200", encoding="utf-8")
    (tmp_path / "fetch_loop.pid").write_text("300", encoding="utf-8")
    issues = supervisor.audit([_lp("watchdog", 100), _lp("bot", 200),
                                  _lp("fetch_loop", 300)])
    severities = [i.severity for i in issues]
    assert "error" not in severities
    assert "warn" not in severities


# ─── 4. Reconcile actions ────────────────────────────────────────────────

def test_reconcile_dry_run_does_not_call_actions(monkeypatch, tmp_path):
    """In dry-run mode, no actual subprocess.run / kill calls happen."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    (tmp_path / "bot.pid").write_text("99999", encoding="utf-8")
    kill_calls = []
    monkeypatch.setattr(supervisor, "_kill_pid",
                          lambda pid: kill_calls.append(pid) or True)
    spawn_calls = []
    monkeypatch.setattr(supervisor, "_spawn_detached",
                          lambda cmd, log: spawn_calls.append(cmd) or 12345)
    # Build issues that would normally trigger fixes
    issues = [
        supervisor.Issue("info", "bot_pid_stale", "x",
                          "delete stale bot.pid"),
        supervisor.Issue("warn", "fetch_loop_dead", "x",
                          "start fetch_loop.py"),
    ]
    actions = supervisor.reconcile(issues, [], [], dry_run=True)
    assert kill_calls == []
    assert spawn_calls == []
    assert all(a.success for a in actions)
    assert all("(dry-run)" in a.description for a in actions)


def test_reconcile_auto_unlinks_stale_bot_pid(monkeypatch, tmp_path):
    """--auto mode actually deletes the stale lockfile."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    pid_file = tmp_path / "bot.pid"
    pid_file.write_text("99999", encoding="utf-8")
    issues = [supervisor.Issue("info", "bot_pid_stale", "x",
                                  "delete stale bot.pid")]
    actions = supervisor.reconcile(issues, [], [], dry_run=False)
    assert not pid_file.exists()
    assert any(a.type == "delete_stale_bot_pid" and a.success
                for a in actions)


def test_reconcile_auto_spawns_dead_fetch_loop(monkeypatch, tmp_path):
    """fetch_loop_dead issue → spawn fetch_loop.py (verified via mock)."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    spawn_calls = []
    monkeypatch.setattr(supervisor, "_spawn_detached",
                          lambda cmd, log: (spawn_calls.append(cmd) or 12345))
    issues = [supervisor.Issue("warn", "fetch_loop_dead", "x",
                                  "start fetch_loop.py")]
    actions = supervisor.reconcile(issues, [], [], dry_run=False)
    assert len(spawn_calls) == 1
    assert "fetch_loop.py" in spawn_calls[0][1]
    assert any(a.type == "spawn_fetch_loop" and a.success
                for a in actions)


def test_reconcile_NEVER_kills_duplicate_bot(monkeypatch, tmp_path):
    """SAFETY-CRITICAL: even in --auto mode, supervisor MUST NOT kill
    bot.py duplicates because positions may be open. Only alert."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    kill_calls = []
    monkeypatch.setattr(supervisor, "_kill_pid",
                          lambda pid: kill_calls.append(pid) or True)
    monkeypatch.setattr(supervisor, "_push_alert",
                          lambda level, title, body: None)
    issues = [supervisor.Issue("error", "bot_duplicate",
                                  "2 bot instances",
                                  "kill duplicate bot.py")]
    procs = []
    logical = [_lp("bot", 100), _lp("bot", 200)]
    actions = supervisor.reconcile(issues, procs, logical, dry_run=False)
    # NO kill calls
    assert kill_calls == [], (
        f"SAFETY VIOLATION: supervisor killed bot.py PIDs {kill_calls}"
    )
    # But did alert
    alert_actions = [a for a in actions
                      if a.type == "alert_only_bot_duplicate"]
    assert len(alert_actions) == 1


def test_reconcile_kills_dup_fetch_loop_keeping_lockfile_owner(
        monkeypatch, tmp_path):
    """When 2 fetch_loops run, kill all EXCEPT the one that owns the lock."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    (tmp_path / "fetch_loop.pid").write_text("200", encoding="utf-8")
    kill_calls = []
    monkeypatch.setattr(supervisor, "_kill_pid",
                          lambda pid: kill_calls.append(pid) or True)
    monkeypatch.setattr(supervisor, "_push_alert",
                          lambda level, title, body: None)
    issues = [supervisor.Issue("error", "fetch_loop_duplicate",
                                  "2 loops",
                                  "kill duplicate fetch_loop")]
    logical = [_lp("fetch_loop", 200),  # lockfile owner
                _lp("fetch_loop", 300)]  # dup
    actions = supervisor.reconcile(issues, [], logical, dry_run=False)
    assert kill_calls == [300]


# ─── 5. End-to-end cycle ────────────────────────────────────────────────

def test_run_one_cycle_returns_summary_dict(monkeypatch, tmp_path):
    """A complete cycle produces a JSONL-shaped summary even with empty
    process list."""
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    monkeypatch.setattr(supervisor, "JSONL_FILE",
                          tmp_path / "supervisor.jsonl")
    monkeypatch.setattr(supervisor, "list_python_processes", lambda: [])
    summary = supervisor.run_one_cycle(dry_run=True)
    assert "ts" in summary
    assert "n_procs" in summary
    assert "issues" in summary
    assert "actions" in summary
    assert summary["dry_run"] is True
    # JSONL must have been appended
    jsonl_text = (tmp_path / "supervisor.jsonl").read_text(encoding="utf-8")
    assert json.loads(jsonl_text.strip())["dry_run"] is True


def test_run_one_cycle_with_clean_state_zero_actions(monkeypatch, tmp_path):
    import supervisor
    monkeypatch.setattr(supervisor, "HERE", tmp_path)
    monkeypatch.setattr(supervisor, "JSONL_FILE",
                          tmp_path / "supervisor.jsonl")
    # Healthy: 1 of each
    (tmp_path / "bot.pid").write_text("200", encoding="utf-8")
    (tmp_path / "fetch_loop.pid").write_text("300", encoding="utf-8")
    monkeypatch.setattr(supervisor, "list_python_processes", lambda: [
        supervisor.ProcInfo(100, 1, "watchdog", False, "python watchdog.py"),
        supervisor.ProcInfo(200, 1, "bot", False, "python bot.py --daemon"),
        supervisor.ProcInfo(300, 1, "fetch_loop", False,
                             "python fetch_loop.py"),
    ])
    summary = supervisor.run_one_cycle(dry_run=False)
    severities = [i["severity"] for i in summary["issues"]]
    assert "error" not in severities
    assert "warn" not in severities
    assert len(summary["actions"]) == 0


# ─── 6. Self-lock wiring ────────────────────────────────────────────────

def test_supervisor_main_uses_phase_65_lockfile():
    """Source-grep: supervisor.main() must self-lock so two supervisors
    can never run at once."""
    src = (ROOT / "06_live_bot" / "supervisor.py").read_text(encoding="utf-8")
    assert "enforce_single_supervisor_or_exit" in src
    assert "release_supervisor_lock" in src
    assert "atexit.register(release_supervisor_lock)" in src


def test_process_lock_exports_supervisor_helpers():
    import process_lock
    assert hasattr(process_lock, "enforce_single_supervisor_or_exit")
    assert hasattr(process_lock, "release_supervisor_lock")
    assert hasattr(process_lock, "SUPERVISOR_LOCKFILE")
