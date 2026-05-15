"""Phase-25: alerter + health_monitor.

Tests cover:
  - LogAlerter writes JSONL with ts/level/title/body
  - Debounce: same (level, title) suppressed within window
  - force=True bypasses debounce
  - TelegramAlerter posts to api.telegram.org with right payload
  - SMTPAlerter calls login + sendmail with right subject/from/to
  - CompositeAlerter sends through all children
  - make_alerter() picks Telegram > SMTP > Log-only based on env
  - HealthMonitor fires alert after N consecutive failures
  - HealthMonitor sends "recovered" alert on transition back to OK
  - All alerter failures swallowed — never raise into the bot
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical  # Phase-25: live-safety gate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _lines(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ─── LogAlerter ─────────────────────────────────────────────────────────────

def test_log_alerter_writes_jsonl(tmp_path):
    from alerter import LogAlerter
    p = tmp_path / "alerts.log"
    a = LogAlerter(path=p, suppress_seconds=0)
    a.send("error", "test-title", body="test body")
    rows = _lines(p)
    assert len(rows) == 1
    assert rows[0]["level"] == "error"
    assert rows[0]["title"] == "test-title"
    assert rows[0]["body"] == "test body"


def test_log_alerter_debounces_duplicate(tmp_path):
    from alerter import LogAlerter
    p = tmp_path / "alerts.log"
    a = LogAlerter(path=p, suppress_seconds=3600)
    assert a.send("error", "same") is True
    assert a.send("error", "same") is False  # suppressed
    rows = _lines(p)
    assert len(rows) == 1


def test_log_alerter_force_bypasses_debounce(tmp_path):
    from alerter import LogAlerter
    p = tmp_path / "alerts.log"
    a = LogAlerter(path=p, suppress_seconds=3600)
    assert a.send("error", "same") is True
    assert a.send("error", "same", force=True) is True
    rows = _lines(p)
    assert len(rows) == 2


def test_log_alerter_creates_parent_dir(tmp_path):
    from alerter import LogAlerter
    deep = tmp_path / "a" / "b" / "alerts.log"
    a = LogAlerter(path=deep, suppress_seconds=0)
    a.send("info", "t")
    assert deep.exists()


# ─── NtfyAlerter ────────────────────────────────────────────────────────────

def test_ntfy_alerter_posts_to_correct_topic():
    from alerter import NtfyAlerter
    seen = {}
    class FakeResp:
        status_code = 200
    def fake_post(url, data=None, headers=None, timeout=None):
        seen["url"] = url
        seen["data"] = data
        seen["headers"] = headers
        return FakeResp()
    a = NtfyAlerter(topic="cameron-bot-test", suppress_seconds=0,
                     http_post=fake_post)
    assert a.send("critical", "blocker", body="something broke") is True
    assert seen["url"] == "https://ntfy.sh/cameron-bot-test"
    assert seen["data"] == b"something broke"
    assert "CRITICAL" in seen["headers"]["Title"]
    assert seen["headers"]["Priority"] == "urgent"


def test_ntfy_alerter_self_host_server_url():
    """Caller can point at a private ntfy server instead of the public
    ntfy.sh service."""
    from alerter import NtfyAlerter
    seen = {}
    class FakeResp:
        status_code = 202
    def fake_post(url, data=None, headers=None, timeout=None):
        seen["url"] = url
        return FakeResp()
    a = NtfyAlerter(topic="x", server="https://my-ntfy.internal:8080",
                     suppress_seconds=0, http_post=fake_post)
    a.send("info", "test")
    assert seen["url"] == "https://my-ntfy.internal:8080/x"


def test_ntfy_alerter_swallows_http_failure():
    from alerter import NtfyAlerter
    def boom(*a, **kw):
        raise RuntimeError("net down")
    a = NtfyAlerter(topic="t", suppress_seconds=0, http_post=boom)
    assert a.send("error", "t") is False


def test_make_alerter_includes_ntfy_when_env_set(tmp_path):
    from alerter import make_alerter, CompositeAlerter, NtfyAlerter
    env = {"NTFY_TOPIC": "cameron-bot-XYZ"}
    a = make_alerter(alerts_log_path=tmp_path / "alerts.log", env=env)
    assert isinstance(a, CompositeAlerter)
    types_in_composite = {type(c).__name__ for c in a.alerters}
    assert "NtfyAlerter" in types_in_composite


# ─── TelegramAlerter ────────────────────────────────────────────────────────

def test_telegram_alerter_posts_to_correct_url():
    from alerter import TelegramAlerter
    seen = {}
    class FakeResp:
        status_code = 200
    def fake_post(url, json=None, timeout=None):
        seen["url"] = url
        seen["payload"] = json
        return FakeResp()
    a = TelegramAlerter(bot_token="TOKEN123", chat_id="CHAT456",
                         suppress_seconds=0, http_post=fake_post)
    assert a.send("critical", "live blocker", body="something is on fire") is True
    assert seen["url"] == "https://api.telegram.org/botTOKEN123/sendMessage"
    assert seen["payload"]["chat_id"] == "CHAT456"
    assert "CRITICAL" in seen["payload"]["text"]
    assert "live blocker" in seen["payload"]["text"]
    assert "something is on fire" in seen["payload"]["text"]


def test_telegram_alerter_swallows_http_failure():
    from alerter import TelegramAlerter
    def boom(*a, **kw):
        raise RuntimeError("network down")
    a = TelegramAlerter(bot_token="X", chat_id="Y",
                         suppress_seconds=0, http_post=boom)
    # Must NOT raise — alerter failures cannot crash the bot
    assert a.send("error", "t") is False


# ─── SMTPAlerter ────────────────────────────────────────────────────────────

def test_smtp_alerter_calls_login_and_sendmail():
    from alerter import SMTPAlerter
    fake_client = MagicMock()
    factory_calls = []
    def factory(host, port):
        factory_calls.append((host, port))
        return fake_client
    a = SMTPAlerter(host="smtp.example.com", port=465,
                     user="me@example.com", password="pw",
                     to_addr="alerts@example.com",
                     suppress_seconds=0, smtp_factory=factory)
    assert a.send("critical", "blocker", body="body text") is True
    assert factory_calls == [("smtp.example.com", 465)]
    fake_client.login.assert_called_once_with("me@example.com", "pw")
    sm_call = fake_client.sendmail.call_args
    assert sm_call.args[0] == "me@example.com"
    assert sm_call.args[1] == ["alerts@example.com"]
    raw = sm_call.args[2]
    assert "Subject: [CAMERON-CRITICAL] blocker" in raw
    assert "body text" in raw


# ─── CompositeAlerter ──────────────────────────────────────────────────────

def test_composite_alerter_sends_to_all(tmp_path):
    from alerter import CompositeAlerter, LogAlerter
    a1 = LogAlerter(path=tmp_path / "a1.log", suppress_seconds=0)
    a2 = LogAlerter(path=tmp_path / "a2.log", suppress_seconds=0)
    c = CompositeAlerter([a1, a2], suppress_seconds=0)
    assert c.send("info", "broadcast") is True
    assert (tmp_path / "a1.log").exists()
    assert (tmp_path / "a2.log").exists()


def test_composite_alerter_survives_child_failure(tmp_path):
    from alerter import CompositeAlerter, LogAlerter
    class Bad:
        name = "bad"
        def send(self, *a, **kw): raise RuntimeError("broken")
    good = LogAlerter(path=tmp_path / "good.log", suppress_seconds=0)
    c = CompositeAlerter([Bad(), good], suppress_seconds=0)
    assert c.send("info", "still ok") is True
    assert (tmp_path / "good.log").exists()


# ─── make_alerter() factory ────────────────────────────────────────────────

def test_make_alerter_returns_log_only_when_no_env(tmp_path):
    from alerter import make_alerter, LogAlerter
    a = make_alerter(alerts_log_path=tmp_path / "alerts.log", env={})
    assert isinstance(a, LogAlerter)


def test_make_alerter_includes_telegram_when_env_set(tmp_path):
    from alerter import make_alerter, CompositeAlerter, TelegramAlerter, LogAlerter
    env = {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "C"}
    a = make_alerter(alerts_log_path=tmp_path / "alerts.log", env=env)
    assert isinstance(a, CompositeAlerter)
    types_in_composite = {type(c).__name__ for c in a.alerters}
    assert "TelegramAlerter" in types_in_composite
    assert "LogAlerter" in types_in_composite


def test_make_alerter_includes_smtp_when_env_set(tmp_path):
    from alerter import make_alerter, CompositeAlerter, SMTPAlerter
    env = {
        "SMTP_HOST": "smtp.x.com", "SMTP_USER": "me", "SMTP_PASS": "pw",
        "SMTP_TO": "alerts@x.com", "SMTP_PORT": "465",
    }
    a = make_alerter(alerts_log_path=tmp_path / "alerts.log", env=env)
    assert isinstance(a, CompositeAlerter)
    types_in_composite = {type(c).__name__ for c in a.alerters}
    assert "SMTPAlerter" in types_in_composite


# ─── HealthMonitor ─────────────────────────────────────────────────────────

def test_health_monitor_fires_after_n_consecutive_failures(tmp_path):
    """Heartbeat probe has threshold=2 → only fires after 2 consecutive
    fails. (Yahoo/Alpaca/news have threshold=1 per Phase-25c — covered
    by test_health_monitor_yfinance_fires_on_first_failure.)"""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=2)
    def bad_heartbeat():
        return ProbeResult("heartbeat", False, "stub-fail")
    mon.probe_heartbeat = bad_heartbeat
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "fresh")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "stub-ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    # First tick: 1 failure, streak=1, no alert
    mon.run_once()
    assert not alerts_log.exists() or len(_lines(alerts_log)) == 0
    # Second tick: streak=2, alert fires
    mon.run_once()
    rows = _lines(alerts_log)
    assert len(rows) == 1
    assert "heartbeat" in rows[0]["title"]
    assert rows[0]["level"] in ("error", "critical")


def test_health_monitor_sends_recovered_on_back_to_ok(tmp_path):
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=1)
    # Other probes always OK
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    # Two states for yfinance: first fail, then recover
    state = {"fail": True}
    def yf():
        return ProbeResult("yfinance",
                            not state["fail"],
                            "bad" if state["fail"] else "back")
    mon.probe_yfinance = yf
    mon.run_once()  # fails, alert fires (n_consecutive=1)
    state["fail"] = False
    mon.run_once()  # recovers, info-alert
    rows = _lines(alerts_log)
    assert any("yfinance unhealthy" in r["title"] for r in rows)
    assert any("recovered" in r["title"] for r in rows)


def test_health_monitor_yfinance_fires_on_first_failure(tmp_path):
    """Phase-25c (user request): yfinance + alpaca + catalyst_news fire
    IMMEDIATELY on first failure (per-probe threshold = 1)."""
    from health_monitor import HealthMonitor, ProbeResult, PROBE_THRESHOLDS
    from alerter import LogAlerter
    assert PROBE_THRESHOLDS["yfinance"] == 1
    assert PROBE_THRESHOLDS["alpaca"] == 1
    assert PROBE_THRESHOLDS["catalyst_news"] == 1
    assert PROBE_THRESHOLDS["heartbeat"] == 2
    assert PROBE_THRESHOLDS["audit"] == 2

    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=2)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", False, "rate-limited")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    # ONE tick should fire because yfinance threshold = 1
    mon.run_once()
    rows = _lines(alerts_log)
    assert any("yfinance unhealthy" in r["title"] for r in rows), \
        "yfinance must alert on FIRST failure, not wait for 2"


def test_health_monitor_alpaca_fires_on_first_failure(tmp_path):
    """alpaca probe with threshold=1 → 1 fail = 1 push."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=2)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "fresh")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", False, "account inactive")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    mon.run_once()
    rows = _lines(alerts_log)
    assert any("alpaca unhealthy" in r["title"] for r in rows)


def test_health_monitor_heartbeat_still_needs_two_failures(tmp_path):
    """heartbeat threshold = 2 → 1 fail = no push, 2 fails = push."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=2)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", False, "missing")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "fresh")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    mon.run_once()  # streak=1, no alert
    rows1 = _lines(alerts_log) if alerts_log.exists() else []
    assert not any("heartbeat unhealthy" in r["title"] for r in rows1)
    mon.run_once()  # streak=2, alert
    rows2 = _lines(alerts_log)
    assert any("heartbeat unhealthy" in r["title"] for r in rows2)


def test_health_monitor_alert_does_not_repeat_while_failing(tmp_path):
    """Phase-25d: WITHIN re_fire_after_sec window, no re-alert. After
    re_fire_after_sec elapsed → push again ("still X unhealthy")."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    # Huge re_fire window so the 5-tick burst stays within it
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=1,
                         re_fire_after_sec=3600)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", False, "still bad")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    for _ in range(5):
        mon.run_once()
    rows = _lines(alerts_log)
    # Only ONE yfinance-unhealthy alert despite 5 consecutive failures
    assert sum(1 for r in rows if "yfinance unhealthy" in r["title"]) == 1


def test_health_monitor_re_fires_after_re_fire_window(tmp_path):
    """Phase-25d: if probe keeps failing for >re_fire_after_sec, a second
    push fires with title 'still X unhealthy'. This is the user's
    'alle 1h neue failure' requirement."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    # Tiny re_fire window (0.1 sec) so the test runs fast
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=1,
                         re_fire_after_sec=1)  # 1 second
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", False, "still bad")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    mon.run_once()  # first failure → first push
    time.sleep(1.2)  # wait past re_fire window
    mon.run_once()  # second failure → re-fire
    rows = _lines(alerts_log)
    titles = [r["title"] for r in rows if "yfinance" in r["title"]]
    assert any(t == "yfinance unhealthy" for t in titles)
    assert any(t == "still yfinance unhealthy" for t in titles)


def test_health_monitor_recovery_resets_re_fire(tmp_path):
    """Phase-25d: recovery clears _last_alert_ts so the next failure
    cycle starts fresh ('recovered' info push + next failure pushes
    immediately, not subject to old re_fire window)."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=1,
                         re_fire_after_sec=3600)
    # Probe toggles: bad, bad, good, bad
    states = iter([False, False, True, False])
    def yf():
        return ProbeResult("yfinance",
                            next(states),
                            "v1" if True else "")
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    mon.probe_yfinance = yf
    mon.run_once()  # fail 1 → push "yfinance unhealthy"
    mon.run_once()  # fail 2 → no push (within re_fire window)
    mon.run_once()  # good → "recovered: yfinance" info
    mon.run_once()  # fail 3 → fresh push "yfinance unhealthy" (not "still")
    rows = _lines(alerts_log)
    titles = [r["title"] for r in rows]
    assert titles.count("yfinance unhealthy") == 2  # two fresh failure cycles
    assert any("recovered: yfinance" in t for t in titles)
    assert not any("still yfinance" in t for t in titles)  # recovery reset the timer


def test_health_monitor_recovery_push_includes_outage_duration(tmp_path):
    """Phase-25d: 'recovered' push body mentions how long the outage lasted."""
    from health_monitor import HealthMonitor, ProbeResult
    from alerter import LogAlerter
    alerts_log = tmp_path / "alerts.log"
    a = LogAlerter(path=alerts_log, suppress_seconds=0)
    mon = HealthMonitor(alerter=a, interval_sec=1, n_consecutive=1,
                         re_fire_after_sec=3600)
    state = {"ok": False}
    def yf():
        return ProbeResult("yfinance", state["ok"],
                            "ok" if state["ok"] else "down")
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "fresh")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "single")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "fresh")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "fresh")
    mon.probe_yfinance = yf
    mon.run_once()  # fail → push
    time.sleep(0.05)
    state["ok"] = True
    mon.run_once()  # recover → push with duration
    rows = _lines(alerts_log)
    recovered = next(r for r in rows if "recovered: yfinance" in r["title"])
    # Body should mention "Outage lasted"
    assert "Outage lasted" in recovered["body"]
