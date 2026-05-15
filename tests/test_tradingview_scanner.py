"""Phase-28: TradingView scanner integration.

Tests cover:
  - scan_cameron_candidates passes filters to tradingview-screener Query
  - DataFrame -> dict translation (ticker dedup of "NASDAQ:AAPL" → "AAPL")
  - safe_float handles None / NaN / non-numeric
  - returns [] on ImportError without raising
  - returns [] on Query() exception without raising
  - md_logger receives "ok" call on success
  - md_logger receives "error" call on failure
  - alpaca_fallback returns gainers when MarketMoversRequest works
  - alpaca_fallback returns [] when alpaca-py too old
  - scan_cameron_top_candidates: TV primary, alpaca fallback when TV empty
  - bot._try_tradingview_primary swallows all errors
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.critical  # Phase-28: primary scanner path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── scan_cameron_candidates ────────────────────────────────────────────────

def test_safe_float_handles_none_and_nan():
    from scanners.tradingview_scanner import _safe_float
    assert _safe_float(None) is None
    assert _safe_float(float("nan")) is None
    assert _safe_float("not-a-number") is None
    assert _safe_float(3.14) == 3.14
    assert _safe_float(0) == 0.0


def test_df_to_rows_strips_exchange_prefix():
    """TradingView returns 'NASDAQ:AAPL' in the ticker column; we strip
    to plain 'AAPL' for downstream uniformity with Alpaca symbols."""
    from scanners.tradingview_scanner import _df_to_rows
    import pandas as pd
    df = pd.DataFrame([
        {"ticker": "NASDAQ:AAPL", "name": "AAPL", "close": 298.5,
         "change": 0.5, "volume": 1_000_000,
         "relative_volume_10d_calc": 1.5,
         "premarket_change": 8.0, "premarket_volume": 500_000,
         "float_shares_outstanding_current": 15_000_000_000,
         "exchange": "NASDAQ"},
    ])
    rows = _df_to_rows(df)
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "AAPL"
    assert r["exchange"] == "NASDAQ"
    assert r["close"] == 298.5
    assert r["premarket_change"] == 8.0
    assert r["rvol_proxy"] == 1.5
    assert r["float_shares"] == 15_000_000_000
    assert r["source"] == "tradingview"


def test_df_to_rows_handles_empty_dataframe():
    from scanners.tradingview_scanner import _df_to_rows
    import pandas as pd
    assert _df_to_rows(pd.DataFrame()) == []
    assert _df_to_rows(None) == []


def test_scan_cameron_candidates_returns_empty_when_pkg_missing(monkeypatch):
    """If tradingview-screener pip pkg isn't installed, must return []
    without raising so the bot's fallback path engages."""
    from scanners import tradingview_scanner as tv
    # Block the import by deleting from sys.modules + intercepting __import__
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **kw):
        if name == "tradingview_screener":
            raise ImportError("module missing for test")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    rows = tv.scan_cameron_candidates(top_n=10)
    assert rows == []


def test_scan_cameron_candidates_swallows_query_exception(monkeypatch):
    """If TradingView's HTTP call fails (network, parse, etc.), the
    function must NOT raise — just log + return []."""
    from scanners import tradingview_scanner as tv
    _install_fake_tv_module(monkeypatch, exc=RuntimeError("TV down"))
    rows = tv.scan_cameron_candidates(top_n=10)
    assert rows == []


def _install_fake_tv_module(monkeypatch, *, df=None, exc=None):
    """Install a tradingview_screener mock that survives the Column
    comparison chain inside scan_cameron_candidates."""
    import sys as _sys
    fake_query = MagicMock()
    fake_query.select.return_value = fake_query
    fake_query.where.return_value = fake_query
    fake_query.order_by.return_value = fake_query
    fake_query.limit.return_value = fake_query
    if exc is not None:
        fake_query.get_scanner_data.side_effect = exc
    else:
        fake_query.get_scanner_data.return_value = (len(df) if df is not None else 0, df)
    # Column needs to support __gt__, __lt__, .between(), .isin() —
    # all return *something* that Query.where can accept (anything works
    # because Query.where is also a MagicMock).
    class FakeColumn:
        def __init__(self, *a, **kw): pass
        def __gt__(self, other): return self
        def __lt__(self, other): return self
        def __ge__(self, other): return self
        def __le__(self, other): return self
        def between(self, lo, hi): return self
        def isin(self, vals): return self
    fake_mod = MagicMock()
    fake_mod.Query = lambda: fake_query
    fake_mod.Column = FakeColumn
    monkeypatch.setitem(_sys.modules, "tradingview_screener", fake_mod)
    return fake_query


def test_scan_cameron_candidates_logs_md_call_on_success(monkeypatch):
    """When md_logger is passed, success path logs source='tradingview',
    call='scan', status='ok', symbol_count=N."""
    from scanners import tradingview_scanner as tv
    import pandas as pd
    fake_df = pd.DataFrame([
        {"ticker": "NASDAQ:AAA", "name": "AAA", "close": 3.0, "change": 5.0,
         "volume": 1_000_000, "relative_volume_10d_calc": 5.0,
         "premarket_change": 15.0, "premarket_volume": 50_000,
         "float_shares_outstanding_current": 5_000_000, "exchange": "NASDAQ"},
    ])
    _install_fake_tv_module(monkeypatch, df=fake_df)
    md_log = MagicMock()
    rows = tv.scan_cameron_candidates(top_n=10, md_logger=md_log)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAA"
    md_log.log_call.assert_called_once()
    kwargs = md_log.log_call.call_args.kwargs
    assert kwargs["source"] == "tradingview"
    assert kwargs["call"] == "scan"
    assert kwargs["status"] == "ok"
    assert kwargs["symbol_count"] == 1


def test_scan_cameron_candidates_logs_md_call_on_error(monkeypatch):
    """Failure path logs source='tradingview', call='scan', status='error',
    error_class=<exception name>."""
    from scanners import tradingview_scanner as tv
    _install_fake_tv_module(monkeypatch, exc=ConnectionError("net"))
    md_log = MagicMock()
    rows = tv.scan_cameron_candidates(top_n=10, md_logger=md_log)
    assert rows == []
    md_log.log_call.assert_called_once()
    kwargs = md_log.log_call.call_args.kwargs
    assert kwargs["source"] == "tradingview"
    assert kwargs["status"] == "error"
    assert kwargs["error_class"] == "ConnectionError"


# ─── Alpaca fallback ────────────────────────────────────────────────────────

def test_alpaca_fallback_returns_gainers_only():
    """alpaca-py MarketMoversRequest returns both gainers and losers;
    we take ONLY gainers (Cameron is long-only)."""
    from scanners.tradingview_scanner import scan_cameron_candidates_alpaca_fallback
    fake_client = MagicMock()
    Mover = type("Mover", (), {})
    def mk(sym, pct, vol, price):
        m = Mover(); m.symbol = sym; m.percent_change = pct
        m.volume = vol; m.price = price
        return m
    movers_obj = MagicMock()
    movers_obj.gainers = [mk("AAA", 12.5, 500000, 3.50),
                           mk("BBB", 8.0, 200000, 5.20)]
    movers_obj.losers = [mk("ZZZ", -10.0, 100000, 1.0)]
    fake_client.get_market_movers.return_value = movers_obj
    rows = scan_cameron_candidates_alpaca_fallback(fake_client, top_n=10)
    syms = [r["ticker"] for r in rows]
    assert "AAA" in syms
    assert "BBB" in syms
    assert "ZZZ" not in syms  # loser excluded
    assert all(r["source"] == "alpaca_fallback" for r in rows)


def test_alpaca_fallback_returns_empty_on_old_alpaca_py(monkeypatch):
    """Old alpaca-py versions don't have MarketMoversRequest. Must
    return [] without raising."""
    from scanners.tradingview_scanner import scan_cameron_candidates_alpaca_fallback
    fake_client = MagicMock()
    fake_client.get_market_movers.side_effect = AttributeError(
        "no MarketMoversRequest")
    # Block the import so our code-path takes the ImportError branch
    import sys as _sys
    real_data_requests = _sys.modules.get("alpaca.data.requests")
    if real_data_requests:
        # Remove MarketMoversRequest from the fake module
        fake_alpaca_req = MagicMock(spec=[])
        monkeypatch.setitem(_sys.modules, "alpaca.data.requests", fake_alpaca_req)
    rows = scan_cameron_candidates_alpaca_fallback(fake_client, top_n=10)
    assert rows == []


# ─── scan_cameron_top_candidates: composite TV→Alpaca ───────────────────────

def test_top_candidates_prefers_tradingview_when_available(monkeypatch):
    """If TV returns rows, alpaca is NOT called (TV is the source of truth)."""
    from scanners import tradingview_scanner as tv
    fake_alpaca = MagicMock()
    monkeypatch.setattr(tv, "scan_cameron_candidates",
                         lambda **kw: [{"ticker": "X", "source": "tradingview"}])
    rows = tv.scan_cameron_top_candidates(top_n=10, alpaca_client=fake_alpaca)
    assert rows == [{"ticker": "X", "source": "tradingview"}]
    fake_alpaca.get_market_movers.assert_not_called()


def test_top_candidates_falls_back_to_alpaca_when_tv_empty(monkeypatch):
    from scanners import tradingview_scanner as tv
    fake_alpaca = MagicMock()
    monkeypatch.setattr(tv, "scan_cameron_candidates", lambda **kw: [])
    monkeypatch.setattr(tv, "scan_cameron_candidates_alpaca_fallback",
                         lambda client, **kw: [{"ticker": "Y", "source": "alpaca_fallback"}])
    rows = tv.scan_cameron_top_candidates(top_n=10, alpaca_client=fake_alpaca)
    assert rows == [{"ticker": "Y", "source": "alpaca_fallback"}]


def test_top_candidates_returns_empty_when_both_fail(monkeypatch):
    from scanners import tradingview_scanner as tv
    monkeypatch.setattr(tv, "scan_cameron_candidates", lambda **kw: [])
    monkeypatch.setattr(tv, "scan_cameron_candidates_alpaca_fallback",
                         lambda client, **kw: [])
    rows = tv.scan_cameron_top_candidates(top_n=10, alpaca_client=MagicMock())
    assert rows == []


# ─── bot._try_tradingview_primary defensive wrapper ────────────────────────

def test_bot_try_tradingview_primary_swallows_all_errors(monkeypatch):
    """The wrapper in bot.py must NEVER raise — its sole job is to
    return [] on any failure so the yfinance fallback engages."""
    import bot
    from scanners import tradingview_scanner as tv
    def boom(**kw):
        raise RuntimeError("everything is on fire")
    monkeypatch.setattr(tv, "scan_cameron_candidates", boom)
    # Reach the function through bot
    rows = bot._try_tradingview_primary(top_n=10)
    assert rows == []
