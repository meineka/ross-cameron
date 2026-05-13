"""Audit-Iter 29 (2026-05-13): audit.py health-monitoring bugs.

Bugs:
  AU-1 (HIGH): tasklist ist Windows-only. Cloud (Linux) hatte silent
    false → bot.process.alive=False → recommendation RESTART_BOT_PROCESS_DEAD.
  AU-2 (HIGH): matched JEDES python.exe (auch audit.py selbst, watchdog,
    replay_today.py) → false alive auch wenn bot.py nicht läuft.
  AU-3 (MED): aggregated memory across ALL python.exe → misleading.
  AU-5 (HIGH): pre-filter blockte INFO-Lines → KeyboardInterrupt-Pattern
    unreachable (es ist log.info in bot.py:1108).
  AU-7 (HIGH): multi-line tracebacks: nur erste Zeile hat Timestamp →
    File-Pfad-Lines + actual Exception-Message wurden gedroppt.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug AU-2: matches only bot.py --daemon ──────────────────────────────────
def test_check_bot_alive_ignores_unrelated_python():
    """audit.py selbst läuft als python — darf NICHT als bot.py alive zählen."""
    import audit
    # Fake psutil with mixed processes
    fake_proc_audit = MagicMock()
    fake_proc_audit.info = {"name": "python", "cmdline": ["python", "audit.py"],
                              "memory_info": MagicMock(rss=50*1024*1024)}
    fake_proc_other = MagicMock()
    fake_proc_other.info = {"name": "python", "cmdline": ["python", "watchdog.py"],
                              "memory_info": MagicMock(rss=20*1024*1024)}
    with patch.object(audit, "subprocess"):  # block fallback
        with patch.dict("sys.modules", {"psutil": MagicMock(
            process_iter=lambda attrs: [fake_proc_audit, fake_proc_other]
        )}):
            alive, mem, count = audit._check_bot_alive_cross_platform()
    assert alive is False, "audit.py selbst darf nicht als bot detected werden"
    assert count == 0


def test_check_bot_alive_detects_real_bot():
    """Echtes bot.py --daemon → alive=True."""
    import audit
    fake_bot = MagicMock()
    fake_bot.info = {
        "name": "python",
        "cmdline": ["python", "bot.py", "--daemon"],
        "memory_info": MagicMock(rss=150*1024*1024),
    }
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [fake_bot]
    )}):
        alive, mem, count = audit._check_bot_alive_cross_platform()
    assert alive is True
    assert count == 1
    assert mem > 0


def test_check_bot_alive_returns_zero_when_no_bot():
    """Kein matching process → False, 0, 0."""
    import audit
    fake_proc = MagicMock()
    fake_proc.info = {"name": "python", "cmdline": ["python", "manage.py"],
                       "memory_info": MagicMock(rss=10*1024*1024)}
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [fake_proc]
    )}):
        alive, mem, count = audit._check_bot_alive_cross_platform()
    assert alive is False
    assert count == 0
    assert mem == 0


def test_check_bot_alive_counts_multiple_bots():
    """2 bot.py-Instances (rare but possible) → count=2."""
    import audit
    fake1 = MagicMock()
    fake1.info = {"name": "python", "cmdline": ["python", "bot.py", "--daemon"],
                   "memory_info": MagicMock(rss=100*1024*1024)}
    fake2 = MagicMock()
    fake2.info = {"name": "python", "cmdline": ["python", "bot.py", "--daemon"],
                   "memory_info": MagicMock(rss=100*1024*1024)}
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [fake1, fake2]
    )}):
        alive, mem, count = audit._check_bot_alive_cross_platform()
    assert count == 2
    assert mem > 100*1024  # both summed


# ─── Bug AU-7: multi-line tracebacks ─────────────────────────────────────────
def test_get_recent_log_lines_includes_traceback_continuations(tmp_path, monkeypatch):
    """Traceback-Folgezeilen (ohne eigenen Timestamp) müssen mit-included werden."""
    import audit
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_content = f"""{now} ERROR Traceback (most recent call last):
  File "bot.py", line 123, in handle_bar
    raise ValueError("test")
ValueError: test
{now} INFO some other log
"""
    log = tmp_path / "daemon.log"
    log.write_text(log_content, encoding="utf-8")
    monkeypatch.setattr(audit, "LOG", log)
    lines = audit.get_recent_log_lines(minutes=5)
    # Sollte den Traceback-Header + 3 Folgezeilen + INFO enthalten
    text = "\n".join(lines)
    assert "Traceback" in text
    assert "File" in text
    assert "ValueError: test" in text


def test_get_recent_log_lines_drops_old_traceback_continuations(tmp_path, monkeypatch):
    """Wenn Traceback-Header außerhalb Window, Folgezeilen NICHT included."""
    import audit
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    log_content = f"""{old} ERROR Traceback (most recent call last):
  File "bot.py", line 123, in handle_bar
ValueError: old error
"""
    log = tmp_path / "daemon.log"
    log.write_text(log_content, encoding="utf-8")
    monkeypatch.setattr(audit, "LOG", log)
    lines = audit.get_recent_log_lines(minutes=30)
    assert len(lines) == 0


# ─── Bug AU-5: INFO lines mit Pattern werden erfasst ─────────────────────────
def test_classify_finds_keyboard_interrupt_in_info_line():
    """KeyboardInterrupt-Pattern existiert, aber log.info → ohne ERROR-Marker.
    Pre-Filter darf das nicht blocken."""
    import audit
    lines = [
        "2026-05-13 18:00:00,000 INFO [bot] KeyboardInterrupt — closing all positions",
    ]
    findings = audit.classify_errors(lines)
    assert len(findings) == 1
    assert findings[0]["category"] == "user_stop"


def test_classify_finds_daily_goal_in_warning_line():
    """SPIRAL-DETECTION ist log.warning → wird via 'WARNING' im prefilter erfasst."""
    import audit
    lines = [
        "2026-05-13 18:00:00,000 WARNING [bot] SPIRAL-DETECTION: 2 consecutive losses",
    ]
    findings = audit.classify_errors(lines)
    assert any(f["category"] == "spiral_lock" for f in findings)


def test_classify_does_not_flood_with_irrelevant_info():
    """Normale INFO-Lines OHNE Pattern dürfen nicht als findings auftauchen."""
    import audit
    lines = [
        "2026-05-13 18:00:00,000 INFO [bot] WS subscribed to 10 symbols",
        "2026-05-13 18:00:01,000 INFO [bot] Health: 0 patterns detected",
    ]
    findings = audit.classify_errors(lines)
    assert findings == []


def test_classify_unknown_only_for_error_lines():
    """ERROR-Line ohne Pattern-Match → 'unknown'. INFO-Line ohne Match → drop."""
    import audit
    lines = [
        "2026-05-13 18:00:00,000 ERROR [bot] Something unexpected happened",
        "2026-05-13 18:00:01,000 INFO [bot] Routine activity",
    ]
    findings = audit.classify_errors(lines)
    assert len(findings) == 1
    assert findings[0]["category"] == "unknown"


# ─── Sanity: cross-platform check returns correct types ──────────────────────
def test_get_bot_status_returns_dict_with_expected_keys():
    import audit
    status = audit.get_bot_status()
    assert "bot_process_alive" in status
    assert "bot_pid_count" in status  # Audit-Iter 29
    assert "bot_memory_mb" in status
    assert "heartbeat_file_age_sec" in status
    assert "disk_free_gb" in status
