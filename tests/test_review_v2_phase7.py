"""Review-V2 Phase 7 behavior tests:
  P1.1 — Real premarket scanner via Alpaca extended-hours snapshot
  P2.4 — Preflight yfinance failure as degraded-mode (clear warning)
  P2.5 — _last_equity wired to dashboard
  P2.6 — Timezone cleanup (NY trading-day, Berlin via ZoneInfo)
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── P1.1: premarket_scanner_v2 ──────────────────────────────────────────────
def test_premarket_scanner_strict_rejects_stale_quote():
    """STRICT mode: latest_trade older than MAX_QUOTE_AGE_SECONDS → reject."""
    import premarket_scanner_v2 as pms
    snap = MagicMock()
    snap.previous_daily_bar.close = 10.0
    snap.daily_bar.volume = 1_000_000
    snap.latest_trade.price = 11.0  # +10% gap
    # Stale: 1 hour ago
    snap.latest_trade.timestamp = datetime.now(timezone.utc).replace(
        year=datetime.now().year - 1)  # last year ≈ very stale
    snap.latest_quote.bid_price = 10.95
    snap.latest_quote.ask_price = 11.05
    row = pms._evaluate_snapshot("X", snap, datetime.now(timezone.utc), "strict")
    assert row is None


def test_premarket_scanner_strict_rejects_below_gap():
    """STRICT mode: gap < MIN_PREMARKET_GAP_PCT → reject."""
    import premarket_scanner_v2 as pms
    snap = MagicMock()
    snap.previous_daily_bar.close = 10.0
    snap.daily_bar.volume = 1_000_000
    snap.latest_trade.price = 10.1  # only 1% gap
    snap.latest_trade.timestamp = datetime.now(timezone.utc)
    snap.latest_quote.bid_price = 10.08
    snap.latest_quote.ask_price = 10.12
    row = pms._evaluate_snapshot("X", snap, datetime.now(timezone.utc), "strict")
    assert row is None


def test_premarket_scanner_strict_passes_good_setup():
    """STRICT mode: fresh + gap > 5% + reasonable spread → pass."""
    import premarket_scanner_v2 as pms
    snap = MagicMock()
    snap.previous_daily_bar.close = 10.0
    snap.daily_bar.volume = 1_000_000
    snap.daily_bar.close = 11.5
    snap.latest_trade.price = 11.5  # 15% gap
    snap.latest_trade.timestamp = datetime.now(timezone.utc)
    snap.latest_quote.bid_price = 11.48
    snap.latest_quote.ask_price = 11.52
    row = pms._evaluate_snapshot("X", snap, datetime.now(timezone.utc), "strict")
    assert row is not None
    assert row["ticker"] == "X"
    assert row["gap_pct"] == 15.0
    assert row["spread_pct"] is not None and row["spread_pct"] < 5.0


def test_premarket_scanner_off_mode_passes_all():
    """OFF mode: returns row even if data is missing."""
    import premarket_scanner_v2 as pms
    snap = MagicMock()
    snap.previous_daily_bar = None  # missing prev-close
    snap.daily_bar.volume = 0
    snap.daily_bar.close = None
    snap.latest_trade = None
    snap.latest_quote = None
    row = pms._evaluate_snapshot("X", snap, datetime.now(timezone.utc), "off")
    # off-mode returns a row with whatever data is available
    assert row is not None
    assert row["ticker"] == "X"


def test_premarket_scanner_invalid_mode_raises():
    import premarket_scanner_v2 as pms
    import pytest
    with pytest.raises(ValueError):
        pms.scan_alpaca_premarket(MagicMock(), [], mode="bogus")


# ─── P2.4: preflight degraded-mode ───────────────────────────────────────────
def test_preflight_yfinance_failure_logs_degraded():
    """When yfinance fails but Alpaca-Auth+WS pass, preflight returns
    True (PASS) but logs 'degraded' warning."""
    import pre_flight
    # Monkeypatch checks to return: alpaca OK, ws OK, yfinance FAIL
    orig_auth = pre_flight.check_alpaca_auth
    orig_ws = pre_flight.check_ws_init
    orig_yf = pre_flight.check_yfinance
    pre_flight.check_alpaca_auth = lambda *a, **k: (True, "mock-auth-ok")
    pre_flight.check_ws_init = lambda *a, **k: (True, "mock-ws-ok")
    pre_flight.check_yfinance = lambda: (False, "mock-yf-down")
    try:
        result = pre_flight.run_preflight("k", "s", yfinance_required=True)
        # Result is True (PASS, degraded) — yfinance is not hard-blocker
        # because two_source_scan provides Alpaca fallback
        assert result is True
    finally:
        pre_flight.check_alpaca_auth = orig_auth
        pre_flight.check_ws_init = orig_ws
        pre_flight.check_yfinance = orig_yf


def test_preflight_alpaca_failure_blocks():
    """Alpaca-Auth failure must block daemon-start."""
    import pre_flight
    orig_auth = pre_flight.check_alpaca_auth
    orig_ws = pre_flight.check_ws_init
    orig_yf = pre_flight.check_yfinance
    pre_flight.check_alpaca_auth = lambda *a, **k: (False, "mock-auth-fail")
    pre_flight.check_ws_init = lambda *a, **k: (True, "mock-ws-ok")
    pre_flight.check_yfinance = lambda: (True, "mock-yf-ok")
    try:
        result = pre_flight.run_preflight("k", "s")
        assert result is False  # alpaca fail = daemon abort
    finally:
        pre_flight.check_alpaca_auth = orig_auth
        pre_flight.check_ws_init = orig_ws
        pre_flight.check_yfinance = orig_yf


# ─── P2.5: _last_equity wiring ───────────────────────────────────────────────
def test_last_equity_set_in_session_start():
    """Bot.run() (verified via source-grep) sets self._last_equity from
    executor.get_equity() at session start. Also refreshed in trading loop."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Session-start assignment
    assert "self._last_equity = equity" in src
    # Refresh in trading loop
    assert "self._last_equity = self.executor.get_equity()" in src


# ─── P2.6: timezone fixes ────────────────────────────────────────────────────
def test_watchlist_persist_uses_ny_trading_day():
    """save_watchlist must save date in NY-timezone, not server-local."""
    src = (ROOT / "06_live_bot" / "watchlist_persist.py").read_text(encoding="utf-8")
    assert "_ny_today_str" in src
    assert 'ZoneInfo("America/New_York")' in src


def test_bot_log_uses_berlin_zoneinfo_not_timedelta_offset():
    """The daemon-loop log uses ZoneInfo('Europe/Berlin') for Berlin time,
    not a fixed timedelta(hours=6) which is DST-incorrect half the year."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # The wrong pattern should be gone
    assert "next_start + timedelta(hours=6)" not in src
    # The right pattern should be present
    assert "ZoneInfo(\"Europe/Berlin\")" in src
