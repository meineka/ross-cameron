"""Phase-21 (ChatGPT-09:15 Task 1): smoke gate — populates the `smoke`
marker which was previously declared but unused.

These tests are pure module-import smoke checks: they verify the
critical 06_live_bot modules import without error and expose the
expected top-level API surface. Total runtime <0.5 s.

Marker: smoke (so `--fast` = `-m "smoke or critical"` includes them).
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_bot_module_imports():
    import bot
    assert hasattr(bot, "ReplayBot")
    assert hasattr(bot, "Bot")
    assert hasattr(bot, "TradeLogger")
    assert hasattr(bot, "TickerState")
    assert hasattr(bot, "DayState")


def test_watchdog_module_imports():
    import watchdog
    assert callable(watchdog.resolve_bot_python)
    assert callable(watchdog.preflight_dependencies)
    assert callable(watchdog.is_bot_running)
    assert callable(watchdog.start_bot)
    assert hasattr(watchdog, "CheckUnknown")


def test_audit_module_imports():
    import audit
    assert callable(audit.classify_bot_processes)
    assert callable(audit.get_bot_status)
    assert callable(audit._collect_bot_processes)


def test_fake_broker_imports_with_expected_behaviors():
    from fake_broker import FakeBroker, FakeOrder
    fb = FakeBroker()
    # Spot-check the behaviors that Phase 17 added so we don't silently
    # lose them in a refactor.
    fb.set_behavior("AAA", "drop_stop_after_fill")
    fb.set_behavior("BBB", "reject_then_market")
    fb.set_behavior("CCC", "stale_quote")
    assert callable(fb.has_stop_protection)
    assert callable(fb.has_target_protection)


def test_no_trade_postmortem_imports():
    import no_trade_postmortem as ntp
    assert callable(ntp.build_postmortem)
    assert callable(ntp.classify_bot_processes) if hasattr(ntp, "classify_bot_processes") else True
    assert callable(ntp._classify_pid_pair)


def test_premarket_scanner_v2_imports():
    import premarket_scanner_v2 as pm
    assert callable(pm.scan_alpaca_premarket_with_reasons)
    assert callable(pm.scan_extended_hours_bars)
    assert callable(pm.merge_premarket_rvol_into_rows)


def test_vwap_filter_imports():
    from vwap_filter import is_above_vwap, session_vwap
    assert callable(is_above_vwap)
    assert callable(session_vwap)


def test_indicators_module_imports():
    from indicators import macd, macd_is_bullish, macd_bear_cross, rsi
    assert callable(macd)
    assert callable(macd_is_bullish)
    assert callable(macd_bear_cross)
    assert callable(rsi)
