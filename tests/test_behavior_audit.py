"""Behavior-Tests die echte Funktion testen (statt source-grep).
Schließt Lücken die der 2026-05-12-Audit aufdeckte.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── secrets_loader: ENV + .env ──────────────────────────────────────────────
def test_secrets_loader_strips_quotes_from_env_file(tmp_path, monkeypatch):
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text('APCA_API_KEY_ID="WRAPPED_KEY"\nAPCA_API_SECRET_KEY=\'WRAPPED_SEC\'\n')
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "WRAPPED_KEY", "quotes should be stripped"
    assert s == "WRAPPED_SEC"


def test_secrets_loader_env_overrides_file(tmp_path, monkeypatch):
    """Env-Vars haben Vorrang vor .env-File. Wichtig für CI."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=FROMFILE\nAPCA_API_SECRET_KEY=FROMFILE_SEC\n")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.setenv("APCA_API_KEY_ID", "FROMENV")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "FROMENV_SEC")
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "FROMENV"
    assert s == "FROMENV_SEC"


def test_secrets_loader_ignores_comments_in_env(tmp_path, monkeypatch):
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("# comment\nAPCA_API_KEY_ID=K\nAPCA_API_SECRET_KEY=S\n# trailing\n")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "K"
    assert s == "S"


# ─── audit.classify_errors: real text-pattern testing ────────────────────────
def test_audit_classify_known_lines():
    """Direkte Behavior-Tests für audit.py — keine source-grep."""
    sys.path.insert(0, str(ROOT / "06_live_bot"))
    import audit
    samples = {
        "ERROR [yfinance] $XYZ: possibly delisted": "yfinance_delisted",
        "ERROR [bot] WS error: 'str' object has no attribute 'value'": "ws_api_drift",
        "ERROR [bot] insufficient buying power": "no_buying_power",
        "ERROR [bot] Alpaca-Connection FAIL": "alpaca_auth",
        "ERROR [bot] NameError in handle_bar": "code_bug",
        "WARNING [bot] SPIRAL-DETECTION: 2 losses": "spiral_lock",
        "WARNING [bot] DAILY GOAL $150 ERREICHT": "goal_reached",
    }
    for line, expected_cat in samples.items():
        found = audit.classify_errors([f"2026-05-12 12:00:00 {line}"])
        assert found, f"no classification for: {line}"
        assert found[0]["category"] == expected_cat, \
            f"{line!r} → expected {expected_cat}, got {found[0]['category']}"


def test_audit_classify_returns_empty_for_info_lines():
    import audit
    found = audit.classify_errors(["2026-05-12 12:00:00 INFO [bot] heartbeat tick"])
    assert found == []


# ─── delisted_cache: thread-state isolation ──────────────────────────────────
def test_delisted_cache_persists_across_process(tmp_path, monkeypatch):
    """Cache muss durchhalten — schreibt zur Datei nach mark."""
    import delisted_cache
    monkeypatch.setattr(delisted_cache, "CACHE_FILE", tmp_path / "dc.json")
    delisted_cache._cache = None
    delisted_cache.mark_delisted("DEAD1")
    # Reset module state simuliert Process-Restart
    delisted_cache._cache = None
    assert delisted_cache.is_delisted("DEAD1") is True


# ─── reconnect_backoff: edge cases ───────────────────────────────────────────
def test_backoff_zero_fails_is_not_circuit_breaker():
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=3)
    # Reset auf 0 darf nicht Circuit-Breaker werfen
    b.reset()
    b.reset()
    assert b.consec_fails == 0


def test_backoff_reset_after_circuit_breaker_recovers():
    """Nach Circuit-Breaker + Reset sollte System wieder funktionieren."""
    from reconnect_backoff import ReconnectBackoff
    b = ReconnectBackoff(max_consec_fails=2, base_sec=1.0, cap_sec=10.0)
    b.fail()
    b.fail()  # consec=2, max=2 → next would be CB
    with pytest.raises(RuntimeError):
        b.fail()  # triggers
    b.reset()
    assert b.fail() == 1.0  # zurück bei base


# ─── slippage_log: file format + roundtrip ───────────────────────────────────
def test_slippage_log_format(tmp_path, monkeypatch):
    import slippage_log
    f = tmp_path / "slip.jsonl"
    monkeypatch.setattr(slippage_log, "SLIP_FILE", f)
    slippage_log.record_fill("AAA", "BUY", 10, 100.0, 100.50)
    line = f.read_text(encoding="utf-8").strip()
    obj = json.loads(line)
    assert obj["symbol"] == "AAA"
    assert obj["drift_pct"] == 0.5
    assert obj["expected"] == 100.0
    assert obj["filled"] == 100.50


# ─── two_source_scan threshold semantics ─────────────────────────────────────
def test_two_source_below_threshold_no_fallback():
    from two_source_scan import should_fallback_to_alpaca
    assert should_fallback_to_alpaca(100, 19) is False  # exactly 19%


def test_two_source_zero_total_no_fallback():
    from two_source_scan import should_fallback_to_alpaca
    # Division by zero guard
    assert should_fallback_to_alpaca(0, 0) is False


# ─── position_recovery: error handling ───────────────────────────────────────
def test_position_recovery_handles_alpaca_error():
    """Wenn get_all_positions wirft, return -1 statt crash."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.side_effect = Exception("API down")
    n = recover_or_flatten(tc)
    assert n == -1


def test_position_recovery_with_real_mode_no_op():
    """mode != 'flatten' soll Positionen nur loggen, nicht schließen."""
    from position_recovery import recover_or_flatten
    tc = MagicMock()
    tc.get_all_positions.return_value = [MagicMock(symbol="AAA", qty="5", avg_entry_price="10")]
    n = recover_or_flatten(tc, mode="report-only")
    assert n == 1
    tc.close_all_positions.assert_not_called()


# ─── vwap_filter: division-by-zero ───────────────────────────────────────────
def test_vwap_zero_volume_returns_none_safe():
    from vwap_filter import session_vwap
    bars = [{"high": 10, "low": 9, "close": 9.5, "volume": 0}]
    assert session_vwap(bars) is None  # graceful


# ─── pump_dump: combined-risk-rule ───────────────────────────────────────────
def test_pump_dump_score_alone_threshold():
    """Score 9999 (< 10k) ist nicht pump-dump trotz extreme combination."""
    from pump_dump_filter import is_pump_dump_risk
    assert is_pump_dump_risk(score=9999) is False


# ─── catalyst_filter: cache behavior ─────────────────────────────────────────
def test_catalyst_uses_cache_to_avoid_re_fetch(monkeypatch):
    """Cache hit → kein yfinance-Call."""
    import catalyst_filter
    import time as _t
    catalyst_filter._cache.clear()
    catalyst_filter._cache["XYZ"] = (True, _t.time())  # fresh cache hit
    with patch("yfinance.Ticker") as mock_ticker:
        result = catalyst_filter.has_recent_news("XYZ")
    assert result is True
    mock_ticker.assert_not_called()
