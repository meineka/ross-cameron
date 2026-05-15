"""Phase-36: 1m bars + provider-explicit STALL/OK alert titles.

USER REQUEST 2026-05-15:
  "mach das in 1m bars"
  "wenn alpaca stalled, Fehler bringen Alpaca stalled und dann wieder
   OK Meldung senden wenn alpaca wieder live, so für alle provider"

Tests:
  1. bot.py BAR_AGGREGATION_MINUTES = 1 (was 5)
  2. PROVIDER_LABELS map exists with all 6 probes
  3. Failure title format: "<PROVIDER> <VERB>"  (e.g. "ALPACA STALLED")
  4. Recovery title format: "<PROVIDER> OK again"
  5. Re-fire title format: "<PROVIDER> STILL <VERB>"
  6. Alpaca, bot_ws both elevate to CRITICAL level
  7. Heartbeat → "BOT FROZEN"
  8. Catalyst → "YAHOO-NEWS DOWN"
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_mon(tmp_path, *, threshold=1):
    from health_monitor import HealthMonitor
    from alerter import LogAlerter
    a = LogAlerter(path=tmp_path / "alerts.log", suppress_seconds=0)
    return HealthMonitor(alerter=a, interval_sec=1, n_consecutive=threshold,
                          re_fire_after_sec=1)


def _read_alerts(tmp_path):
    p = tmp_path / "alerts.log"
    if not p.exists():
        return []
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


def test_bot_aggregates_1min_bars_not_5min():
    """USER request: 1m bars. Verify constant flipped."""
    import bot
    assert bot.BAR_AGGREGATION_MINUTES == 1, (
        f"BAR_AGGREGATION_MINUTES={bot.BAR_AGGREGATION_MINUTES} (expected 1 for 1m bars)"
    )


def test_provider_labels_map_covers_all_probes():
    """Every probe routed by run_once() must have a PROVIDER_LABELS entry."""
    from health_monitor import PROVIDER_LABELS
    required = {"alpaca", "bot_ws", "yfinance", "catalyst_news",
                "heartbeat", "audit"}
    assert required.issubset(set(PROVIDER_LABELS.keys()))


def test_alpaca_failure_pushes_alpaca_stalled(tmp_path):
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", False, "auth lost")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()
    rows = _read_alerts(tmp_path)
    assert any(r["title"] == "ALPACA STALLED" for r in rows), \
        f"missing 'ALPACA STALLED' in {[r['title'] for r in rows]}"
    # Alpaca is critical level
    critical_rows = [r for r in rows if r["title"] == "ALPACA STALLED"]
    assert critical_rows[0]["level"] == "critical"


def test_alpaca_recovery_pushes_alpaca_ok_again(tmp_path):
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    state = {"fail": True}
    def alp():
        return ProbeResult("alpaca", not state["fail"],
                            "auth lost" if state["fail"] else "auth ok")
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = alp
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()  # fail → push
    state["fail"] = False
    mon.run_once()  # recover → push
    rows = _read_alerts(tmp_path)
    titles = [r["title"] for r in rows]
    assert "ALPACA STALLED" in titles
    assert "ALPACA OK again" in titles


def test_yahoo_failure_recovery_cycle(tmp_path):
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    state = {"fail": True}
    def yf():
        return ProbeResult("yfinance", not state["fail"],
                            "blocked" if state["fail"] else "ok")
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = yf
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "ok")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()
    state["fail"] = False
    mon.run_once()
    titles = [r["title"] for r in _read_alerts(tmp_path)]
    assert "YAHOO DOWN" in titles
    assert "YAHOO OK again" in titles


def test_heartbeat_failure_renders_as_bot_frozen(tmp_path):
    """heartbeat threshold=2 — need 2 fails then push."""
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path, threshold=2)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", False, "stale 600s")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "ok")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()
    mon.run_once()
    titles = [r["title"] for r in _read_alerts(tmp_path)]
    assert "BOT FROZEN" in titles


def test_bot_ws_failure_renders_as_alpaca_ws_stalled(tmp_path):
    """bot_ws (Phase-34 probe) maps to ALPACA-WS provider."""
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "ok")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", False,
                                             "20 WS-errors in last 500 lines")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()
    rows = _read_alerts(tmp_path)
    titles = [r["title"] for r in rows]
    assert "ALPACA-WS STALLED" in titles
    # bot_ws is critical level too
    critical = [r for r in rows if r["title"] == "ALPACA-WS STALLED"]
    assert critical[0]["level"] == "critical"


def test_catalyst_news_failure_renders_as_yahoo_news_down(tmp_path):
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", True, "ok")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", False,
                                                    "news pipeline empty")
    mon.run_once()
    titles = [r["title"] for r in _read_alerts(tmp_path)]
    assert "YAHOO-NEWS DOWN" in titles


def test_re_fire_renders_as_still_stalled(tmp_path):
    """After re_fire_after_sec while still failing → STILL prefix."""
    from health_monitor import ProbeResult
    mon = _make_mon(tmp_path)
    mon.probe_heartbeat = lambda: ProbeResult("heartbeat", True, "ok")
    mon.probe_audit_recommendation = lambda: ProbeResult("audit", True, "ok")
    mon.probe_yfinance = lambda: ProbeResult("yfinance", True, "ok")
    mon.probe_alpaca = lambda: ProbeResult("alpaca", False, "still down")
    mon.probe_bot_ws = lambda: ProbeResult("bot_ws", True, "ok")
    mon.probe_catalyst_news = lambda: ProbeResult("catalyst_news", True, "ok")
    mon.run_once()                          # first push: ALPACA STALLED
    time.sleep(1.2)                         # past re_fire (1s)
    mon.run_once()                          # re-fire: ALPACA STILL STALLED
    titles = [r["title"] for r in _read_alerts(tmp_path)]
    assert "ALPACA STALLED" in titles
    assert "ALPACA STILL STALLED" in titles
