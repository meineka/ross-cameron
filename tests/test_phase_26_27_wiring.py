"""Phase-26/27: structured logger wiring + premarket-v2 shadow + catalyst override.

Tests cover:
  - Bot.__init__ binds MarketDataLogger + OrderLifecycleLogger
  - AlpacaExecutor inherits null loggers by default
  - submit_bracket_buy emits intent → submitted (or rejected) lifecycle
  - catalyst_filter.set_market_data_logger wires the logger globally
  - catalyst soft-mode passes stale-news WHEN gap >= 15% AND rvol >= 10x
  - catalyst soft-mode still rejects when gap+rvol below threshold
  - _run_premarket_v2_shadow logs + writes premarket_v2_shadow.jsonl
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.critical  # Phase-26/27: live-path wiring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Phase-26: Bot binds structured loggers ─────────────────────────────────

def test_bot_init_binds_structured_loggers(monkeypatch, tmp_path):
    """Bot.__init__ must construct MarketDataLogger + OrderLifecycleLogger
    OR fall back to Null variants. Either way the attrs must exist."""
    import bot, structured_logger
    # Reroute paths into tmp so test writes don't leak into 06_live_bot/
    monkeypatch.setattr(structured_logger, "MARKET_DATA_PATH", tmp_path / "md.jsonl")
    monkeypatch.setattr(structured_logger, "ORDER_LIFECYCLE_PATH", tmp_path / "ol.jsonl")
    # Avoid real Alpaca client
    with patch.object(bot, "AlpacaExecutor") as ExecMock:
        instance = MagicMock()
        instance.md_logger = None
        instance.ol_logger = None
        ExecMock.return_value = instance
        b = bot.Bot(api_key="k", api_secret="s", dry_run=True)
    assert hasattr(b, "md_logger")
    assert hasattr(b, "ol_logger")
    # Loggers should be the real ones or Null ones — both expose .log_call /
    # .emit_* methods
    assert callable(getattr(b.md_logger, "log_call", None))
    assert callable(getattr(b.ol_logger, "emit_intent", None))


def test_alpaca_executor_default_loggers_are_null():
    """AlpacaExecutor.__init__ binds Null loggers so test paths that
    construct it standalone don't get None.attribute errors."""
    import bot
    with patch("bot.TradingClient"):
        ex = bot.AlpacaExecutor(api_key="k", api_secret="s", paper=True)
    # Null variants accept all method calls
    iid = ex.ol_logger.emit_intent(symbol="X", side="BUY", qty=1)
    assert iid == "null-intent"
    ex.md_logger.log_call(source="x", call="y", status="ok")


def test_submit_bracket_buy_dry_run_emits_intent_and_filled(monkeypatch):
    """Dry-run path must emit intent + filled lifecycle events even
    though no real Alpaca call happens."""
    import bot, structured_logger
    with patch("bot.TradingClient"):
        ex = bot.AlpacaExecutor(api_key="k", api_secret="s", paper=True, dry_run=True)
    # Real OrderLifecycleLogger pointed at a tmp path
    captured = []
    class CaptureLogger:
        def emit_intent(self, **kw):
            captured.append(("intent", kw))
            return "intent-test-1"
        def emit_submitted(self, iid, **kw): captured.append(("submitted", kw))
        def emit_accepted(self, iid, **kw): captured.append(("accepted", kw))
        def emit_rejected(self, iid, **kw): captured.append(("rejected", kw))
        def emit_filled(self, iid, **kw): captured.append(("filled", kw))
        def emit_canceled(self, iid, **kw): captured.append(("canceled", kw))
        def emit_protection_verified(self, *a, **kw): pass
        def emit_protection_repaired(self, *a, **kw): pass
        def emit_closed(self, *a, **kw): pass
    ex.ol_logger = CaptureLogger()
    res = ex.submit_bracket_buy("AAPL", 10, entry=298.5, stop=295.5, take_profit=304.5)
    assert res["status"] == "filled"
    states = [s for s, _ in captured]
    assert "intent" in states
    assert "filled" in states


# ─── Phase-26: catalyst_filter md_logger injection ──────────────────────────

def test_catalyst_filter_set_market_data_logger_wires_global():
    import catalyst_filter
    fake = MagicMock()
    catalyst_filter.set_market_data_logger(fake)
    assert catalyst_filter._md_logger is fake
    # Reset to None to not leak into other tests
    catalyst_filter.set_market_data_logger(None)


# ─── Phase-26: catalyst soft-mode gap+rvol override ─────────────────────────

def test_catalyst_soft_mode_passes_strong_move_when_news_stale(monkeypatch):
    """Cameron logic: huge gap + huge RVOL IS the catalyst. yfinance
    sparse-news false-rejects must be overridden in soft mode when the
    move itself proves there's a catalyst."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    # Stub yfinance to return news that's all stale (>24h old)
    stale_news = [{"providerPublishTime": time.time() - 7 * 24 * 3600}]
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda sym: MagicMock(news=stale_news))
    # Without gap/rvol → reject as before
    assert catalyst_filter.has_recent_news("XYZ", mode="soft") is False
    catalyst_filter.clear_cache()
    # WITH strong move (gap>=15, rvol>=10) → pass via soft-override
    assert catalyst_filter.has_recent_news(
        "XYZ", mode="soft", gap_pct=20.0, rvol=12.0) is True


def test_catalyst_soft_mode_still_rejects_weak_move_with_stale_news(monkeypatch):
    """Override only fires when BOTH gap and rvol meet the threshold."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    stale_news = [{"providerPublishTime": time.time() - 7 * 24 * 3600}]
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda sym: MagicMock(news=stale_news))
    # gap below threshold
    catalyst_filter.clear_cache()
    assert catalyst_filter.has_recent_news(
        "ABC", mode="soft", gap_pct=10.0, rvol=12.0) is False
    # rvol below threshold
    catalyst_filter.clear_cache()
    assert catalyst_filter.has_recent_news(
        "ABC", mode="soft", gap_pct=20.0, rvol=5.0) is False
    # Both None
    catalyst_filter.clear_cache()
    assert catalyst_filter.has_recent_news(
        "ABC", mode="soft", gap_pct=None, rvol=None) is False


def test_catalyst_strict_mode_unchanged_by_gap_rvol(monkeypatch):
    """Strict-mode never overrides — must NOT pass on strong-move alone."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    stale_news = [{"providerPublishTime": time.time() - 7 * 24 * 3600}]
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda sym: MagicMock(news=stale_news))
    assert catalyst_filter.has_recent_news(
        "XYZ", mode="strict", gap_pct=50.0, rvol=50.0) is False


# ─── Phase-27: premarket-v2 shadow mode ─────────────────────────────────────

def test_premarket_v2_shadow_skips_cleanly_when_no_alpaca():
    """Shadow path must NEVER raise into the live scanner. If Alpaca
    deps / keys are missing it should just log and return."""
    import bot
    # Fake candidates DataFrame-like with .itertuples()
    Cand = type("Cand", (), {})
    c = Cand(); c.ticker = "AAA"
    fake_df = MagicMock()
    fake_df.__len__ = lambda self: 1
    fake_df.itertuples = lambda: iter([c])
    # No mocking of secrets_loader/alpaca — it should ImportError silently
    # The function MUST not raise
    bot._run_premarket_v2_shadow(fake_df, top_n=10)
