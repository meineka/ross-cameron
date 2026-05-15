"""Phase-13 (ChatGPT-20:11 P1): no_trade_postmortem produces a
machine-readable diagnosis for no-trade days.

Asserts:
  - build_postmortem returns the documented field schema
  - missing status.json / logs degrade gracefully (None / empty defaults)
  - final_reason_no_trade synthesizer picks an actionable line per scenario
  - write_postmortem creates a file at the expected path
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


REQUIRED_FIELDS = {
    "schema_version", "generated_at_utc", "target_date_ny",
    "bot_daemon_alive", "bot_daemon_pids", "bot_daemon_pid_pairs",
    "watchdog_alive", "watchdog_pids", "watchdog_pid_pairs",
    "last_watchdog_error", "last_watchdog_error_raw_unfiltered",
    "last_bot_start", "last_ws_subscription",
    "status_json_ts", "status_json_stale_seconds", "status_json_parse_error",
    "heartbeat_file_age_sec", "heartbeat_content",
    "last_scan_time", "pre_rank_candidates", "watchlist",
    "reject_counts_by_reason", "pattern_reject_counts",
    "orders_submitted", "final_reason_no_trade",
}


@pytest.fixture(autouse=True)
def isolate_heartbeat_file(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    monkeypatch.setattr(ntp, "HEARTBEAT_FILE", tmp_path / "heartbeat.txt")


def test_build_postmortem_returns_required_schema(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    # Point all artifact paths at empty tmp dir → everything missing
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "status.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "daemon.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "watchdog.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "trades_live.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    doc = ntp.build_postmortem("2026-05-14")
    missing = REQUIRED_FIELDS - set(doc.keys())
    assert not missing, f"missing fields: {missing}"
    assert doc["target_date_ny"] == "2026-05-14"
    assert doc["schema_version"] == 1


def test_postmortem_graceful_on_missing_inputs(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    doc = ntp.build_postmortem("2026-05-14")
    assert doc["bot_daemon_alive"] is False
    assert doc["watchdog_alive"] is False
    assert doc["last_watchdog_error"] is None
    assert doc["last_bot_start"] is None
    assert doc["status_json_ts"] is None
    assert doc["status_json_stale_seconds"] is None
    assert doc["heartbeat_file_age_sec"] is None
    assert doc["heartbeat_content"] is None
    assert doc["orders_submitted"] == 0
    assert doc["watchlist"] is None


def test_postmortem_picks_up_watchdog_error(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    wd_log = tmp_path / "watchdog.log"
    wd_log.write_text(
        "2026-05-14 19:08:00 INFO Bot OK\n"
        "2026-05-14 19:13:00 WARNING Bot NOT running\n"
        "2026-05-14 19:13:00 ERROR Watchdog: position-check failed - abort restart: No module named 'alpaca'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", wd_log)
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [99999])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    doc = ntp.build_postmortem("2026-05-14")
    assert "No module named 'alpaca'" in (doc["last_watchdog_error"] or "")
    assert doc["watchdog_alive"] is True
    assert "watchdog last error" in doc["final_reason_no_trade"]


def test_postmortem_detects_stale_status_json(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    sj = tmp_path / "status.json"
    # ts well in the past so stale_seconds is huge
    sj.write_text(json.dumps({
        "ts": "2026-05-13T10:00:00",
        "trades_today": 0,
        "watchlist": [],
    }), encoding="utf-8")
    monkeypatch.setattr(ntp, "STATUS_JSON", sj)
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "x.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "x.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "x.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [123])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    doc = ntp.build_postmortem("2026-05-14")
    assert doc["status_json_stale_seconds"] is not None
    assert doc["status_json_stale_seconds"] > 1800
    assert "stale" in doc["final_reason_no_trade"]


def test_postmortem_fresh_heartbeat_suppresses_stale_status_false_hang(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    sj = tmp_path / "status.json"
    sj.write_text(json.dumps({
        "ts": "2026-05-13T10:00:00",
        "trades_today": 0,
        "watchlist": [],
    }), encoding="utf-8")
    hb = tmp_path / "heartbeat.txt"
    hb.write_text("2026-05-14 16:24:27 NY", encoding="utf-8")
    monkeypatch.setattr(ntp, "STATUS_JSON", sj)
    monkeypatch.setattr(ntp, "HEARTBEAT_FILE", hb)
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "x.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "x.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "x.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [123])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    doc = ntp.build_postmortem("2026-05-14")
    assert doc["status_json_stale_seconds"] is not None
    assert doc["heartbeat_file_age_sec"] is not None
    assert doc["heartbeat_file_age_sec"] <= 1800
    assert "possible hang" not in doc["final_reason_no_trade"]
    assert "fresh heartbeat" in doc["final_reason_no_trade"]


def test_postmortem_counts_orders_in_live_log(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    live = tmp_path / "trades_live.jsonl"
    live.write_text(
        json.dumps({"ts": "2026-05-14T10:00:00Z", "event": "entry", "symbol": "AAA"}) + "\n" +
        json.dumps({"ts": "2026-05-14T10:30:00Z", "event": "T1", "symbol": "AAA"}) + "\n" +
        json.dumps({"ts": "2026-05-13T10:00:00Z", "event": "entry", "symbol": "OLD"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", live)
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [42])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    doc = ntp.build_postmortem("2026-05-14")
    assert doc["orders_submitted"] == 2
    # Final reason should reflect that this was NOT a no-trade day
    assert "NOT a no-trade day" in doc["final_reason_no_trade"]


def test_postmortem_final_reason_bot_dead(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    doc = ntp.build_postmortem("2026-05-14")
    assert "dead" in doc["final_reason_no_trade"]


def test_postmortem_extracts_pattern_rejects_from_summary(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    sm = tmp_path / "day_summary_2026-05-14.json"
    sm.write_text(json.dumps({
        "patterns_rejected_vwap": 4,
        "patterns_rejected_macd": 7,
        "reject_catalyst": 12,
        "unrelated_field": "ignored",
    }), encoding="utf-8")
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [1])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    doc = ntp.build_postmortem("2026-05-14")
    counts = doc["pattern_reject_counts"]
    assert counts.get("patterns_rejected_vwap") == 4
    assert counts.get("patterns_rejected_macd") == 7
    assert counts.get("reject_catalyst") == 12
    assert "unrelated_field" not in counts


def test_write_postmortem_creates_file(monkeypatch, tmp_path):
    import no_trade_postmortem as ntp
    monkeypatch.setattr(ntp, "HERE", tmp_path)
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [])
    out = ntp.write_postmortem("2026-05-14")
    assert out.exists()
    assert out.name == "no_trade_postmortem_20260514.json"
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["target_date_ny"] == "2026-05-14"


# ─── Watchdog --preflight-only mode (Phase-13) ───────────────────────────────

def test_watchdog_preflight_only_returns_0_when_deps_ok(monkeypatch, capsys):
    import watchdog
    monkeypatch.setattr(watchdog, "resolve_bot_python", lambda: "fake.exe")
    monkeypatch.setattr(watchdog, "preflight_dependencies",
                         lambda py, deps=watchdog.REQUIRED_DEPS: (True, []))
    rc = watchdog.preflight_only()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Preflight OK" in out


def test_watchdog_preflight_only_returns_1_when_deps_missing(monkeypatch, capsys):
    import watchdog
    monkeypatch.setattr(watchdog, "resolve_bot_python", lambda: "fake.exe")
    monkeypatch.setattr(watchdog, "preflight_dependencies",
                         lambda py, deps=watchdog.REQUIRED_DEPS: (False, ["alpaca", "yfinance"]))
    rc = watchdog.preflight_only()
    assert rc == 1
    out = capsys.readouterr().out
    assert "DEPENDENCY PREFLIGHT FAILED" in out
    assert "alpaca" in out


# ─── Phase-15 (ChatGPT-08:11 #3): postmortem polish ──────────────────────────

def test_postmortem_filters_errors_before_last_restart(monkeypatch, tmp_path):
    """Errors logged BEFORE the most recent WATCHDOG START / Preflight OK
    / Started bot.py must NOT surface as last_watchdog_error — that's a
    stale error from a previous run, not the current cause."""
    import no_trade_postmortem as ntp
    wd_log = tmp_path / "watchdog.log"
    wd_log.write_text(
        "2026-05-14 19:08:00 INFO Bot OK\n"
        "2026-05-14 19:13:00 WARNING Bot NOT running\n"
        "2026-05-14 19:13:00 ERROR Watchdog: position-check failed - abort restart: No module named 'alpaca'\n"
        "2026-05-15 06:00:00 INFO WATCHDOG START - checks every 300 sec\n"
        "2026-05-15 06:00:01 INFO Preflight OK - deps importable\n"
        "2026-05-15 06:05:00 INFO Bot OK - PIDs [12345]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", wd_log)
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [12345])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [9999])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(ntp, "_get_parent_pid_map", lambda: {})
    doc = ntp.build_postmortem("2026-05-15")
    # Filtered field: None (no errors AFTER the 06:00 restart)
    assert doc["last_watchdog_error"] is None, \
        "Stale 19:13 error must be filtered out after 06:00 restart"
    # Raw unfiltered field: still surfaces the old error for debugging
    assert "No module named 'alpaca'" in (doc["last_watchdog_error_raw_unfiltered"] or "")


def test_postmortem_surfaces_errors_after_last_restart(monkeypatch, tmp_path):
    """If an ERROR was logged AFTER the most recent WATCHDOG START, it
    must surface — that's a real current issue."""
    import no_trade_postmortem as ntp
    wd_log = tmp_path / "watchdog.log"
    wd_log.write_text(
        "2026-05-15 06:00:00 INFO WATCHDOG START - checks every 300 sec\n"
        "2026-05-15 06:00:01 INFO Preflight OK\n"
        "2026-05-15 06:30:00 ERROR Restart failed: some new problem\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", wd_log)
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [9999])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(ntp, "_get_parent_pid_map", lambda: {})
    doc = ntp.build_postmortem("2026-05-15")
    assert "some new problem" in (doc["last_watchdog_error"] or "")


def test_postmortem_classifies_launcher_child_pair_as_single():
    """A python.exe launcher (e.g. venv Scripts\\python.exe) that spawns
    a child python.exe should be flagged as ONE process pair, not two
    independent processes."""
    import no_trade_postmortem as ntp
    pids = [39148, 46932]
    parent_map = {46932: 39148, 39148: 12345}  # 39148 spawned 46932
    out = ntp._classify_pid_pair(pids, parent_map)
    assert out["process_pairs"] == [{"launcher": 39148, "child": 46932}]
    assert out["standalone_pids"] == []
    assert "single venv launcher/child pair" in out["interpretation"]


def test_postmortem_classifies_two_independent_processes():
    """Two python.exe processes with no parent-child relationship between
    them must be flagged as independent."""
    import no_trade_postmortem as ntp
    pids = [10001, 20002]
    parent_map = {10001: 5000, 20002: 6000}  # different parents
    out = ntp._classify_pid_pair(pids, parent_map)
    assert out["process_pairs"] == []
    assert sorted(out["standalone_pids"]) == [10001, 20002]
    assert "2 independent processes" in out["interpretation"]


def test_postmortem_single_pid_is_standalone():
    import no_trade_postmortem as ntp
    out = ntp._classify_pid_pair([12345], {12345: 0})
    assert out["process_pairs"] == []
    assert out["standalone_pids"] == [12345]
    assert "single standalone" in out["interpretation"]


def test_postmortem_build_includes_pid_pair_classification(monkeypatch, tmp_path):
    """End-to-end: build_postmortem emits bot_daemon_pid_pairs and
    watchdog_pid_pairs fields."""
    import no_trade_postmortem as ntp
    monkeypatch.setattr(ntp, "STATUS_JSON", tmp_path / "nope.json")
    monkeypatch.setattr(ntp, "DAEMON_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "WATCHDOG_LOG", tmp_path / "nope.log")
    monkeypatch.setattr(ntp, "TRADES_LIVE_JSONL", tmp_path / "nope.jsonl")
    monkeypatch.setattr(ntp, "DAY_SUMMARY_DIR", tmp_path)
    monkeypatch.setattr(ntp, "_find_bot_daemon_pids", lambda: [100, 200])
    monkeypatch.setattr(ntp, "_find_watchdog_pids", lambda: [300])
    monkeypatch.setattr(ntp, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(ntp, "_get_parent_pid_map", lambda: {200: 100, 100: 50, 300: 1})
    doc = ntp.build_postmortem("2026-05-15")
    assert "bot_daemon_pid_pairs" in doc
    assert doc["bot_daemon_pid_pairs"]["process_pairs"] == [{"launcher": 100, "child": 200}]
    assert "watchdog_pid_pairs" in doc
    assert doc["watchdog_pid_pairs"]["standalone_pids"] == [300]
