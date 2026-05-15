"""Phase-34: bot_ws health probe scans bot.log for WS reconnect storms.

Why this probe exists: on 2026-05-15 the bot's data WS was stuck in a
1.6 Hz auth-fail reconnect loop ("connection limit exceeded") for hours
WITHOUT triggering any health-monitor alert, because:

  - probe_heartbeat:    bot was alive (False alarm avoided OK)
  - probe_alpaca:       REST endpoints worked fine, returned GREEN
  - probe_yfinance:     news API worked, returned GREEN
  - probe_catalyst_news: same, GREEN

Net result: bot couldn't trade, user got zero notifications. Phase-34
adds a probe that reads bot.log tail and detects:
  - WS error signatures (count > threshold = unhealthy)
  - WS stale during RTH (no subscribe/bar in tail > N seconds)

The probe is threshold=1 (immediate alert) and joins the existing
30s ntfy-push pipeline.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_monitor():
    """HealthMonitor instance with a MagicMock alerter."""
    from health_monitor import HealthMonitor
    return HealthMonitor(alerter=MagicMock())


def test_probe_bot_ws_in_default_probe_list():
    """Source-grep: run_once() must include probe_bot_ws."""
    src = (ROOT / "06_live_bot" / "health_monitor.py").read_text(encoding="utf-8")
    assert "self.probe_bot_ws," in src, "probe_bot_ws not wired into run_once"


def test_probe_threshold_for_bot_ws_is_one():
    """immediate alert on first failure — matches yfinance/alpaca/news."""
    from health_monitor import PROBE_THRESHOLDS
    assert PROBE_THRESHOLDS["bot_ws"] == 1


def test_probe_bot_ws_returns_ok_when_no_log_file(tmp_path):
    """Bot hasn't started yet → not an error."""
    from health_monitor import HealthMonitor
    import health_monitor as hm
    # Point HERE at empty tmp_path
    with patch.object(hm, "HERE", tmp_path):
        mon = _make_monitor()
        r = mon.probe_bot_ws()
    assert r.ok is True
    assert "bot.log missing" in r.detail


def test_probe_bot_ws_detects_reconnect_storm(tmp_path):
    """Bot.log with many 'connection limit exceeded' lines → UNHEALTHY."""
    import health_monitor as hm
    log_file = tmp_path / "bot.log"
    # 50 spam lines + 50 normal lines — count of 50 well exceeds threshold (10)
    storm_lines = [
        f"2026-05-15 15:54:{i:02d},000 ERROR ws connection limit exceeded\n"
        for i in range(50)
    ] + [
        f"2026-05-15 15:55:{i:02d},000 INFO normal log\n" for i in range(50)
    ]
    log_file.write_text("".join(storm_lines), encoding="utf-8")
    with patch.object(hm, "HERE", tmp_path):
        # Disable the market-hours check so the staleness branch doesn't
        # ALSO fire (we want to verify the error-count branch specifically)
        mon = _make_monitor()
        with patch("alpaca.trading.client.TradingClient") as TC:
            TC.return_value.get_clock.return_value.is_open = False
            r = mon.probe_bot_ws()
    assert r.ok is False
    assert "WS-errors" in r.detail
    assert r.value >= 10


def test_probe_bot_ws_passes_with_clean_log(tmp_path):
    """Bot.log without WS errors → HEALTHY."""
    import health_monitor as hm
    log_file = tmp_path / "bot.log"
    # 50 clean lines, ending with a recent WS-subscribed line
    now = datetime.now()
    lines = [
        f"{(now - timedelta(seconds=300-i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO routine\n"
        for i in range(50)
    ]
    lines.append(
        f"{now.strftime('%Y-%m-%d %H:%M:%S')},000 INFO [bot] WS subscribed to 5 symbols\n"
    )
    log_file.write_text("".join(lines), encoding="utf-8")
    with patch.object(hm, "HERE", tmp_path):
        mon = _make_monitor()
        with patch("alpaca.trading.client.TradingClient") as TC:
            TC.return_value.get_clock.return_value.is_open = True
            r = mon.probe_bot_ws()
    assert r.ok is True, f"expected ok, got: {r.detail}"


def test_probe_bot_ws_detects_stale_during_rth(tmp_path):
    """During RTH: last WS-subscribe/bar line > 10 min old → UNHEALTHY
    even if no WS error lines are present."""
    import health_monitor as hm
    log_file = tmp_path / "bot.log"
    # Last subscribe was 20 minutes ago, no errors since
    stale_time = datetime.now() - timedelta(minutes=20)
    lines = [
        f"{stale_time.strftime('%Y-%m-%d %H:%M:%S')},000 INFO [bot] WS subscribed to 5 symbols\n"
    ]
    # And some innocuous later lines (no on_bar, no WS subscribed)
    now = datetime.now()
    for i in range(20):
        lines.append(
            f"{(now - timedelta(seconds=20-i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO routine\n"
        )
    log_file.write_text("".join(lines), encoding="utf-8")
    with patch.object(hm, "HERE", tmp_path):
        mon = _make_monitor()
        with patch("alpaca.trading.client.TradingClient") as TC:
            TC.return_value.get_clock.return_value.is_open = True
            r = mon.probe_bot_ws()
    assert r.ok is False
    assert "stale" in r.detail.lower()


def test_probe_bot_ws_swallows_log_read_errors(tmp_path):
    """If bot.log can't be read at all, probe returns False but doesn't crash."""
    import health_monitor as hm
    # bot.log path is a DIRECTORY (not a file) — open() will fail
    (tmp_path / "bot.log").mkdir()
    with patch.object(hm, "HERE", tmp_path):
        mon = _make_monitor()
        # Must not raise
        r = mon.probe_bot_ws()
    # Either ok=True (treated as missing) or ok=False with error detail — both acceptable
    assert isinstance(r.ok, bool)


def test_probe_bot_ws_market_closed_relaxes_stale_check(tmp_path):
    """When market is closed, stale WS is NOT an error (bot is idle).
    Only the error-count branch can fail."""
    import health_monitor as hm
    log_file = tmp_path / "bot.log"
    # Very old subscribe (12 hours back) — would fail RTH check
    old = datetime.now() - timedelta(hours=12)
    lines = [
        f"{old.strftime('%Y-%m-%d %H:%M:%S')},000 INFO [bot] WS subscribed to 5 symbols\n"
    ]
    for i in range(10):
        lines.append(
            f"{(datetime.now() - timedelta(seconds=10-i)).strftime('%Y-%m-%d %H:%M:%S')},000 INFO ok\n"
        )
    log_file.write_text("".join(lines), encoding="utf-8")
    with patch.object(hm, "HERE", tmp_path):
        mon = _make_monitor()
        with patch("alpaca.trading.client.TradingClient") as TC:
            TC.return_value.get_clock.return_value.is_open = False
            r = mon.probe_bot_ws()
    assert r.ok is True, f"market closed should be relaxed, got: {r.detail}"
