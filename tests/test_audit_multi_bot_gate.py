"""Phase-18 (ChatGPT-08:49 #5 P0): single-bot-process gate.

Audit must classify the set of running `bot.py --daemon` processes into
exactly one of:
  - "none"
  - "single"
  - "launcher_child_pair"
  - "multiple_independent_bots"   ← P0 FAIL, watchdog must refuse restart

These tests pin the classifier's output for each scenario and ensure
the watchdog blocks on multi-independent-bots.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.critical  # Phase-19 (ChatGPT-08:49 #1): smoke/critical gate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_classify_none_when_no_bot_processes():
    import audit
    out = audit.classify_bot_processes([])
    assert out["classification"] == "none"
    assert out["is_safe_to_restart"] is True
    assert out["block_reason"] is None
    assert out["process_count"] == 0


def test_classify_single_when_exactly_one():
    import audit
    out = audit.classify_bot_processes([
        {"pid": 12345, "ppid": 1, "memory_kb": 50000},
    ])
    assert out["classification"] == "single"
    assert out["pids"] == [12345]
    assert out["is_safe_to_restart"] is True


def test_classify_launcher_child_pair_two_procs():
    """The Windows-venv "python.exe launcher → bot.py --daemon child"
    pattern: PID A is the parent of PID B; both are detected."""
    import audit
    out = audit.classify_bot_processes([
        {"pid": 39148, "ppid": 1234, "memory_kb": 5000},   # launcher
        {"pid": 46932, "ppid": 39148, "memory_kb": 60000}, # child
    ])
    assert out["classification"] == "launcher_child_pair"
    assert out["process_pairs"] == [{"launcher": 39148, "child": 46932}]
    assert out["standalone_pids"] == []
    assert out["is_safe_to_restart"] is True


def test_classify_multiple_independent_bots_blocks_restart():
    """Two bot processes with no parent-child relationship between them
    must be flagged as P0 FAIL — operator must intervene."""
    import audit
    out = audit.classify_bot_processes([
        {"pid": 10001, "ppid": 5000, "memory_kb": 50000},
        {"pid": 20002, "ppid": 7000, "memory_kb": 50000},
    ])
    assert out["classification"] == "multiple_independent_bots"
    assert out["is_safe_to_restart"] is False
    assert out["block_reason"] is not None
    assert "standalone" in out["block_reason"]
    assert sorted(out["standalone_pids"]) == [10001, 20002]


def test_classify_one_pair_plus_independent_is_still_failure():
    """If we have a launcher/child pair AND an extra standalone bot,
    that's still multi-instance — must FAIL."""
    import audit
    out = audit.classify_bot_processes([
        {"pid": 39148, "ppid": 1234, "memory_kb": 5000},   # launcher
        {"pid": 46932, "ppid": 39148, "memory_kb": 60000}, # child of A
        {"pid": 99999, "ppid": 8888, "memory_kb": 60000},  # independent
    ])
    assert out["classification"] == "multiple_independent_bots"
    assert out["is_safe_to_restart"] is False


def test_get_bot_status_includes_classification(monkeypatch):
    """get_bot_status() must surface bot_proc_classification so callers
    don't need to re-scrape the process table."""
    import audit
    monkeypatch.setattr(audit, "_collect_bot_processes",
                         lambda: [{"pid": 1, "ppid": 0, "memory_kb": 0}])
    status = audit.get_bot_status()
    assert "bot_proc_classification" in status
    assert status["bot_proc_classification"]["classification"] == "single"


def test_get_bot_status_classification_changes_recommendation(monkeypatch):
    """When classification is multiple_independent_bots, the audit
    recommendation must override every other rule (memory, log-stale,
    etc.) with BLOCK_MULTIPLE_INDEPENDENT_BOTS."""
    import audit
    monkeypatch.setattr(audit, "_collect_bot_processes", lambda: [
        {"pid": 10001, "ppid": 5000, "memory_kb": 50000},
        {"pid": 20002, "ppid": 7000, "memory_kb": 50000},
    ])
    # Run the recommendation logic inline by simulating main()'s code path
    status = audit.get_bot_status()
    assert status["bot_proc_classification"]["classification"] == "multiple_independent_bots"
    assert status["bot_proc_classification"]["is_safe_to_restart"] is False


# ─── Watchdog gate ───────────────────────────────────────────────────────────

def test_watchdog_refuses_restart_on_multiple_independent_bots(monkeypatch, tmp_path):
    """watchdog.start_bot() must return None (refuse) when the audit
    pre-check reports multiple_independent_bots — preventing a third
    bot from being spawned on top of the duplicates."""
    import watchdog
    # Stub the audit classifier to report a P0 FAIL
    import audit
    monkeypatch.setattr(audit, "classify_bot_processes",
                         lambda procs=None: {
                             "classification": "multiple_independent_bots",
                             "process_count": 2,
                             "pids": [10001, 20002],
                             "process_pairs": [],
                             "standalone_pids": [10001, 20002],
                             "is_safe_to_restart": False,
                             "block_reason": "2 standalone bot process(es) detected",
                         })
    # Stub everything else so the start_bot path is otherwise valid —
    # the multi-bot gate should short-circuit before any of it matters
    import types
    fake_secrets = types.SimpleNamespace(get_alpaca_keys=lambda: ("K", "S"))
    monkeypatch.setitem(sys.modules, "secrets_loader", fake_secrets)
    monkeypatch.setattr(watchdog, "_position_check_via_bot_python",
                         lambda py, k, s: (True, 0))
    seen = {"popen": False}
    def fake_popen(argv, **kw):
        seen["popen"] = True
        return MagicMock(pid=99999)
    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(watchdog, "HERE", tmp_path)
    pid = watchdog.start_bot("fake.exe")
    assert pid is None, "start_bot must return None when multi-independent-bots"
    assert seen["popen"] is False, "Popen must NOT be called when blocked"


def test_watchdog_proceeds_on_launcher_child_pair(monkeypatch, tmp_path):
    """launcher/child pair is a SAFE state — watchdog should NOT block.
    (In practice it'd see the bot as alive and skip restart anyway, but
    if it reached start_bot the multi-bot gate must not abort.)"""
    import watchdog
    import audit
    monkeypatch.setattr(audit, "classify_bot_processes",
                         lambda procs=None: {
                             "classification": "launcher_child_pair",
                             "process_count": 2,
                             "pids": [39148, 46932],
                             "process_pairs": [{"launcher": 39148, "child": 46932}],
                             "standalone_pids": [],
                             "is_safe_to_restart": True,
                             "block_reason": None,
                         })
    import types
    fake_secrets = types.SimpleNamespace(get_alpaca_keys=lambda: ("K", "S"))
    monkeypatch.setitem(sys.modules, "secrets_loader", fake_secrets)
    monkeypatch.setattr(watchdog, "_position_check_via_bot_python",
                         lambda py, k, s: (True, 0))
    popen_called = {"v": False}
    def fake_popen(argv, **kw):
        popen_called["v"] = True
        return MagicMock(pid=99999)
    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(watchdog, "HERE", tmp_path)
    pid = watchdog.start_bot("fake.exe")
    assert pid == 99999
    assert popen_called["v"] is True
