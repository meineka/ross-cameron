"""Tests für die 14 Verbesserungen nach 2026-05-11 Post-Mortem."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── #1 Pre-Flight ───────────────────────────────────────────────────────────
def test_preflight_module_present():
    import pre_flight
    assert hasattr(pre_flight, "run_preflight")
    assert hasattr(pre_flight, "check_ws_init")


def test_preflight_ws_init_passes_with_enum():
    import pre_flight
    ok, msg = pre_flight.check_ws_init("dummy", "dummy")
    assert ok, msg
    assert "iex" in msg.lower()


def test_preflight_alpaca_auth_fails_with_dummy():
    import pre_flight
    ok, msg = pre_flight.check_alpaca_auth("dummy", "dummy")
    assert not ok
    assert "FAIL" in msg


# ─── #2 Watchlist-Persist ────────────────────────────────────────────────────
def test_watchlist_persist_roundtrip(tmp_path, monkeypatch):
    import watchlist_persist as wp
    monkeypatch.setattr(wp, "WATCHLIST_FILE", tmp_path / "wl.json")
    wp.save_watchlist(["AAA", "BBB"], {"AAA": 1.5, "BBB": 0.5})
    got = wp.load_watchlist_if_fresh()
    assert got == ["AAA", "BBB"]


def test_watchlist_returns_none_if_stale(tmp_path, monkeypatch):
    import watchlist_persist as wp
    p = tmp_path / "wl.json"
    p.write_text(json.dumps({"date": "1999-01-01", "symbols": ["X"]}))
    monkeypatch.setattr(wp, "WATCHLIST_FILE", p)
    assert wp.load_watchlist_if_fresh() is None


def test_watchlist_returns_none_if_missing(tmp_path, monkeypatch):
    import watchlist_persist as wp
    monkeypatch.setattr(wp, "WATCHLIST_FILE", tmp_path / "missing.json")
    assert wp.load_watchlist_if_fresh() is None


# ─── #3 Reconnect-Backoff ────────────────────────────────────────────────────
def test_backoff_exponential():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=60.0, max_consec_fails=8)
    assert b.fail() == 1.0
    assert b.fail() == 2.0
    assert b.fail() == 4.0
    assert b.fail() == 8.0


def test_backoff_cap():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=10.0, max_consec_fails=20)
    for _ in range(10):
        d = b.fail()
    assert d == 10.0  # cap


def test_backoff_circuit_breaker():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(base_sec=1.0, cap_sec=60.0, max_consec_fails=3)
    b.fail(); b.fail(); b.fail()
    with pytest.raises(RuntimeError, match="Circuit-Breaker"):
        b.fail()


def test_backoff_reset():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=3)
    b.fail(); b.fail()
    b.reset()
    assert b.consec_fails == 0
    assert b.fail() == 1.0  # back to base


# ─── #4 Position-Recovery ────────────────────────────────────────────────────
def test_position_recovery_flatten_calls_close_all():
    """Audit-Iter 6: stateful mock — position fades after close_all submitted."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    state = {"closed": False}

    def list_side(*a, **kw):
        if state["closed"]:
            return []
        return [MagicMock(symbol="AAA", qty="10", avg_entry_price="5.0")]

    def close_side(*a, **kw):
        state["closed"] = True

    tc.get_all_positions.side_effect = list_side
    tc.close_all_positions.side_effect = close_side
    n = recover_or_flatten(tc, verify_timeout_sec=0.5, poll_interval_sec=0.05)
    assert n == 1
    tc.close_all_positions.assert_called_with(cancel_orders=True)


def test_position_recovery_no_positions():
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = []
    assert recover_or_flatten(tc) == 0
    tc.close_all_positions.assert_not_called()


# ─── #6 Slippage ─────────────────────────────────────────────────────────────
def test_slippage_drift_pct():
    from slippage_log import record_fill, SLIP_FILE
    if SLIP_FILE.exists():
        SLIP_FILE.unlink()
    entry = record_fill("XXX", "BUY", 1, expected=10.0, filled=10.05)
    assert entry["drift_pct"] == 0.5
    assert entry["drift_abs"] == 0.05


def test_slippage_log_writes_jsonl():
    from slippage_log import record_fill, SLIP_FILE
    if SLIP_FILE.exists():
        SLIP_FILE.unlink()
    record_fill("YYY", "SELL", 2, 5.0, 4.99)
    assert SLIP_FILE.exists()
    last = SLIP_FILE.read_text(encoding="utf-8").splitlines()[-1]
    parsed = json.loads(last)
    assert parsed["symbol"] == "YYY"


# ─── #7 VWAP-Filter ──────────────────────────────────────────────────────────
def test_vwap_basic():
    from vwap_filter import session_vwap, is_above_vwap
    bars = [
        {"high": 10, "low": 9, "close": 9.5, "volume": 100},
        {"high": 11, "low": 10, "close": 10.5, "volume": 100},
    ]
    v = session_vwap(bars)
    assert 9.5 < v < 10.5
    assert is_above_vwap(bars, 12.0) is True
    assert is_above_vwap(bars, 8.0) is False


def test_vwap_empty_no_veto():
    from vwap_filter import is_above_vwap
    assert is_above_vwap([], 10.0) is True


# ─── #8 Two-Source ───────────────────────────────────────────────────────────
def test_two_source_threshold():
    from two_source_scan import should_fallback_to_alpaca
    assert should_fallback_to_alpaca(100, 25) is True  # 25%
    assert should_fallback_to_alpaca(100, 10) is False
    assert should_fallback_to_alpaca(0, 0) is False


# ─── #9 Status-Dashboard ─────────────────────────────────────────────────────
def test_status_writes_json(tmp_path, monkeypatch):
    import status_dashboard as sd
    monkeypatch.setattr(sd, "STATUS_FILE", tmp_path / "status.json")
    bot = MagicMock()
    bot.day.realized_pnl = 12.34
    bot.day.peak_pnl = 50.0
    bot.day.spy_pct_today = 0.5
    bot.day.consecutive_losses = 0
    bot.day.spiral_locked = False
    bot.day.ws_reconnects = 2
    bot.tickers = {}
    bot._last_equity = 100000
    sd.write_status(bot)
    assert (tmp_path / "status.json").exists()
    payload = json.loads((tmp_path / "status.json").read_text())
    assert payload["realized_pnl"] == 12.34


# ─── #11 Secrets-Loader ──────────────────────────────────────────────────────
def test_secrets_loader_raises_without_keys(monkeypatch):
    import secrets_loader
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    monkeypatch.setattr(secrets_loader, "ENV_FILE", Path("/nonexistent/.env"))
    with pytest.raises(RuntimeError, match="missing"):
        secrets_loader.get_alpaca_keys()


def test_secrets_loader_from_env(monkeypatch):
    import secrets_loader
    monkeypatch.setenv("APCA_API_KEY_ID", "K")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "S")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", Path("/nonexistent/.env"))
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "K" and s == "S"


def test_no_hardcoded_secret_in_repo():
    """Keine API-Keys mehr im Code. .env darf, ist gitignored.
    Audit-Iter 4 12.05: scan ALLE .py-Files in 06_live_bot, nicht nur Whitelist —
    watchdog.py war übersehen und hatte hardcoded keys."""
    bot_dir = ROOT / "06_live_bot"
    for py_file in bot_dir.rglob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        assert "PKBERNOMU23XEGRU5SPD3JZGDX" not in src, f"hardcoded key in {py_file.name}"
        assert "FZBBx9v8Pw7eaLRFD8wW51WNnVkWeWNkts2D7zRSaxaB" not in src, f"hardcoded secret in {py_file.name}"


# ─── #12 Day-Summary persist ─────────────────────────────────────────────────
def test_day_summary_persist(tmp_path, monkeypatch):
    import day_summary_persist as dsp
    monkeypatch.setattr(dsp, "RESULTS_DIR", tmp_path)
    day = MagicMock()
    day.realized_pnl = 25.50
    day.peak_pnl = 30.00
    day.bars_received = 100
    day.patterns_detected = 5
    day.patterns_rejected_macd = 1
    day.patterns_rejected_fbo = 1
    day.patterns_rejected_pullback_count = 0
    day.patterns_rejected_size_zero = 0
    day.orders_submitted = 3
    day.orders_failed = 0
    day.consecutive_losses = 1
    day.spiral_locked = False
    day.ws_reconnects = 0
    out = dsp.write_day_summary(day, spy_pct=0.5)
    assert out.exists()
    p = json.loads(out.read_text())
    assert p["realized_pnl"] == 25.50
    assert p["spy_pct"] == 0.5


# ─── #14 Float-Filter ────────────────────────────────────────────────────────
def test_float_filter_unknown_passes(monkeypatch):
    import float_filter
    monkeypatch.setattr(float_filter, "_cache", {"ZZZ": None})
    assert float_filter.passes_float_filter("ZZZ") is True  # unknown → pass


def test_float_filter_small_passes(monkeypatch):
    import float_filter
    monkeypatch.setattr(float_filter, "_cache", {"AAA": 5_000_000})
    assert float_filter.passes_float_filter("AAA") is True


def test_float_filter_large_fails(monkeypatch):
    import float_filter
    monkeypatch.setattr(float_filter, "_cache", {"BIG": 500_000_000})
    assert float_filter.passes_float_filter("BIG") is False


# ─── #16 Alpha-Proxy in day-summary ──────────────────────────────────────────
def test_day_summary_includes_alpha(tmp_path, monkeypatch):
    import day_summary_persist as dsp
    monkeypatch.setattr(dsp, "RESULTS_DIR", tmp_path)
    day = MagicMock()
    for k in ["realized_pnl", "peak_pnl", "bars_received", "patterns_detected",
              "patterns_rejected_macd", "patterns_rejected_fbo",
              "patterns_rejected_pullback_count", "patterns_rejected_size_zero",
              "orders_submitted", "orders_failed", "consecutive_losses",
              "ws_reconnects"]:
        setattr(day, k, 0)
    day.spiral_locked = False
    out = dsp.write_day_summary(day, spy_pct=1.0)
    p = json.loads(out.read_text())
    assert "alpha_proxy" in p


# ─── #10 CI ──────────────────────────────────────────────────────────────────
def test_github_actions_workflow_exists():
    wf = ROOT / ".github" / "workflows" / "tests.yml"
    assert wf.exists(), "GitHub Actions CI workflow fehlt"
    src = wf.read_text(encoding="utf-8")
    assert "pytest" in src
