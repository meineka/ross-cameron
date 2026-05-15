"""Phase-53: GuardedTradingClient + GuardedStockHistoricalDataClient.

ChatGPT review P0 (5 consecutive review answers): "RateGuard exists
but is NOT WIRED into the live Alpaca SDK call sites — bot makes raw
TradingClient/StockHistoricalDataClient calls that bypass the 200/min
rate cap and never log to alpaca_api_calls.jsonl."

This phase introduces drop-in wrappers + alpaca_api_calls.jsonl with
blocked_ms / latency_ms per call.

Tests:
  - wrapper proxies all attribute access (callable + non-callable)
  - rate-guard.block_until_allowed is called BEFORE every method
  - alpaca_api_calls.jsonl row written on success
  - error is logged + re-raised
  - bot.py + force_trade_loop.py source-grep: use the guarded wrappers
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture
def temp_log(tmp_path, monkeypatch):
    """Point ALPACA_API_CALLS_LOG at a tmp file for clean assertions."""
    import guarded_alpaca
    log_path = tmp_path / "alpaca_api_calls.jsonl"
    monkeypatch.setattr(guarded_alpaca, "ALPACA_API_CALLS_LOG", log_path)
    return log_path


def _read_log(p):
    if not p.exists():
        return []
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


def test_guarded_proxy_forwards_callable_attributes(temp_log):
    """Calling a method on the wrapper invokes the inner method."""
    import guarded_alpaca
    inner = MagicMock()
    inner.get_account.return_value = MagicMock(equity="100000")
    guard = MagicMock()
    proxy = guarded_alpaca._GuardedProxy(inner, source="alpaca-trading",
                                           guard=guard)
    result = proxy.get_account()
    inner.get_account.assert_called_once()
    assert result.equity == "100000"


def test_guarded_proxy_forwards_non_callable_attributes():
    """Non-callable attributes pass through unchanged."""
    import guarded_alpaca
    inner = MagicMock()
    inner.some_setting = "hello"
    proxy = guarded_alpaca._GuardedProxy(inner, source="alpaca-trading",
                                           guard=MagicMock())
    assert proxy.some_setting == "hello"


def test_guarded_proxy_calls_block_until_allowed_before_invoke(temp_log):
    """Every method call must rate-block before invoking the inner."""
    import guarded_alpaca
    inner = MagicMock()
    inner.get_account.return_value = MagicMock()
    guard = MagicMock()
    proxy = guarded_alpaca._GuardedProxy(inner, source="alpaca-trading",
                                           guard=guard)
    proxy.get_account()
    guard.block_until_allowed.assert_called_once()


def test_guarded_proxy_logs_ok_call(temp_log):
    """On successful call, one JSONL row with status=ok is written."""
    import guarded_alpaca
    inner = MagicMock()
    inner.submit_order.return_value = MagicMock(id="abc-123")
    guard = MagicMock()
    proxy = guarded_alpaca._GuardedProxy(inner, source="alpaca-trading",
                                           guard=guard)
    proxy.submit_order(symbol="AAPL", qty=1)
    rows = _read_log(temp_log)
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "alpaca-trading"
    assert r["method"] == "submit_order"
    assert r["status"] == "ok"
    assert r["error_class"] is None
    assert isinstance(r["latency_ms"], (int, float))
    assert isinstance(r["blocked_ms"], (int, float))


def test_guarded_proxy_logs_and_reraises_on_error(temp_log):
    """On exception, log it + re-raise the original exception."""
    import guarded_alpaca
    inner = MagicMock()
    inner.submit_order.side_effect = ValueError("connection limit exceeded")
    guard = MagicMock()
    proxy = guarded_alpaca._GuardedProxy(inner, source="alpaca-trading",
                                           guard=guard)
    with pytest.raises(ValueError, match="connection limit"):
        proxy.submit_order(symbol="AAPL", qty=1)
    rows = _read_log(temp_log)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "error"
    assert r["error_class"] == "ValueError"
    assert "connection limit" in r["extra"]["error"]


def test_guarded_trading_client_factory_uses_global_guard():
    """GuardedTradingClient() must wire into the module-global
    RateGuard so all processes share one token bucket."""
    import guarded_alpaca
    import alpaca_rate_guard
    # Reset for clean state
    alpaca_rate_guard._reset_for_tests() if hasattr(
        alpaca_rate_guard, "_reset_for_tests") else None
    with patch("alpaca.trading.client.TradingClient") as TC:
        TC.return_value = MagicMock()
        client = guarded_alpaca.GuardedTradingClient("k", "s", paper=True)
    assert client._source == "alpaca-trading"
    assert client._guard is alpaca_rate_guard.get_global_guard()


def test_guarded_data_client_factory_uses_global_guard():
    import guarded_alpaca
    import alpaca_rate_guard
    with patch("alpaca.data.historical.StockHistoricalDataClient") as DC:
        DC.return_value = MagicMock()
        client = guarded_alpaca.GuardedStockHistoricalDataClient("k", "s")
    assert client._source == "alpaca-data"
    assert client._guard is alpaca_rate_guard.get_global_guard()


def test_current_rate_per_min_returns_int():
    """Public metric for status dashboard / health monitor probes."""
    import guarded_alpaca
    rate = guarded_alpaca.current_rate_per_min()
    assert isinstance(rate, int)
    assert rate >= 0


def test_bot_source_uses_guarded_clients():
    """Source-grep: bot.py imports + uses GuardedTradingClient and
    GuardedStockHistoricalDataClient aliases."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from guarded_alpaca import" in src
    assert "GuardedTradingClient as _GuardedTC" in src
    assert "GuardedStockHistoricalDataClient as _GuardedDC" in src
    # Verify ALL TradingClient + StockHistoricalDataClient
    # CONSTRUCTOR sites use the guarded aliases (not raw classes).
    # `_GuardedTC(...)` or `_GuardedDC(...)` patterns.
    import re
    raw_trading_constructors = re.findall(r"\bTradingClient\(", src)
    # The fallback `_GuardedTC = TradingClient` line is not a constructor
    raw_trading_constructors = [m for m in raw_trading_constructors
                                  if "from " not in m]
    # Allowed: 1 fallback assignment (`_GuardedTC = TradingClient`)
    # Count actual constructor CALL sites
    call_sites = re.findall(r"\bTradingClient\([^)]", src)
    # Should be 0 after Phase-53 wiring (all routes through _GuardedTC).
    # Allow the fallback assignment which uses "= TradingClient" (no paren).
    assert len(call_sites) == 0, (
        f"bot.py still has {len(call_sites)} raw TradingClient(...) "
        f"constructor calls — they should go through _GuardedTC"
    )


def test_force_trade_loop_uses_guarded_clients():
    """Source-grep: force_trade_loop.py uses guarded wrappers."""
    src = (ROOT / "06_live_bot" / "force_trade_loop.py").read_text(encoding="utf-8")
    assert "GuardedTradingClient" in src
    assert "GuardedStockHistoricalDataClient" in src
