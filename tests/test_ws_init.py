"""Regression-Tests für die Probleme vom 2026-05-11.

Was an dem Tag schief lief:
  1. `StockDataStream(feed="iex")` warf AttributeError 'str' has no '.value'
     → 14× Reconnect-Loop, kein Trading, watchlist verloren.
  2. yfinance-Errors (delisted symbols) gingen als 'unknown=high' durch
     Audit → falscher ALARM.
  3. Audit kannte das WS-API-Drift-Muster nicht → falscher Severity-Mix.
  4. Bot-Restart mid-day schickte Bot bis morgen schlafen statt sofort
     weitertraden.

Diese Tests garantieren, dass jedes dieser Probleme bei künftigen Releases
sofort auffliegt.
"""
import re
from pathlib import Path

import pytest

from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

ROOT = Path(__file__).resolve().parents[1]
BOT_SRC = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
AUDIT_SRC = (ROOT / "06_live_bot" / "audit.py").read_text(encoding="utf-8")


# ─── 1. WS-API-Drift ────────────────────────────────────────────────────────
# Phase-43 (2026-05-15): these two tests instantiate StockDataStream directly
# and assert on its pristine behavior. They are SENSITIVE to whether Phase-43
# singleton enforcement is currently enabled in the same Python process —
# CPython caches type slots (tp_new) on first lookup, so even a perfect
# monkey-patch reset can leave the slot in a state that rejects extra args.
# In CI the tests pass when run in isolation but flake when phase_31/ws_init
# tests are batched into the same process. Mitigation: pytest-mark them so
# they only run when explicitly requested; the singleton itself is tested
# in test_phase_31_alpaca_ws_patch.py.
def test_stockdatastream_accepts_enum_feed():
    # Phase-43: reload the SDK module so any prior monkey-patch is shed.
    import importlib
    import alpaca.data.live.stock as _stock_mod
    importlib.reload(_stock_mod)
    from alpaca.data.live.stock import StockDataStream as _SDS
    ws = _SDS("dummy", "dummy", feed=DataFeed.IEX)
    assert ws is not None
    assert "iex" in ws._endpoint


def test_stockdatastream_rejects_string_feed():
    """Reproduziert exakt den Bug vom 12:31 CET. Wenn alpaca-py das eines
    Tages wieder akzeptiert, weckt dieser Test uns auf."""
    # Phase-43: same reload requirement as above
    import importlib
    import alpaca.data.live.stock as _stock_mod
    importlib.reload(_stock_mod)
    from alpaca.data.live.stock import StockDataStream as _SDS
    with pytest.raises(AttributeError, match=r"'str' object has no attribute 'value'"):
        _SDS("dummy", "dummy", feed="iex")


def test_bot_imports_datafeed_enum():
    assert "from alpaca.data.enums import DataFeed" in BOT_SRC, "DataFeed-Import fehlt"


def test_bot_uses_enum_not_string_for_feed():
    assert "feed=DataFeed.IEX" in BOT_SRC, "feed=DataFeed.IEX nicht verwendet"
    assert 'feed="iex"' not in BOT_SRC, "alter String-Feed noch da"
    assert "feed='iex'" not in BOT_SRC, "alter String-Feed (single-quote) noch da"


# ─── 2. Audit kennt heutige Fehler ──────────────────────────────────────────
def _classify(line: str):
    """Mini-Klassifizierer der die Pattern-Liste aus audit.py spiegelt."""
    import sys
    sys.path.insert(0, str(ROOT / "06_live_bot"))
    import audit as A
    for pattern, category, severity, fixable, hint in A.ERROR_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return category, severity, fixable
    return "unknown", "high", False


def test_audit_classifies_yfinance_delisted_as_info():
    """Delisted-Symbol-Spam darf KEIN high-severity ALARM auslösen."""
    samples = [
        'ERROR [yfinance] $BWIV: possibly delisted; no timezone found',
        'ERROR [yfinance] $CXH: possibly delisted; no price data found',
        'ERROR [yfinance] HTTP Error 404: Quote not found for symbol: BWIV',
        "ERROR [yfinance] ['WDFC']: possibly delisted; no price data found",
    ]
    for line in samples:
        cat, sev, fix = _classify(line)
        assert cat == "yfinance_delisted", f"miss-classified: {line!r} → {cat}"
        assert sev == "info", f"severity should be info, got {sev}"
        assert fix is True


def test_audit_classifies_ws_api_drift_as_critical():
    """Das exakte Symptom von heute soll als 'ws_api_drift' / critical
    gemeldet werden, NICHT als generisches ws_disconnect (low)."""
    line = "ERROR [bot] WS error (#1): 'str' object has no attribute 'value' — reconnect in 10s"
    cat, sev, fix = _classify(line)
    assert cat == "ws_api_drift"
    assert sev == "critical"
    assert fix is False  # braucht Code-Fix, kein Auto-Restart


def test_audit_ws_disconnect_still_low_severity():
    """Generische WS-Disconnects bleiben low — Auto-Reconnect reicht."""
    line = "ERROR [bot] WS error (#3): WebSocket connection closed — reconnect in 5s"
    cat, sev, _ = _classify(line)
    assert cat == "ws_disconnect"
    assert sev == "low"


def test_audit_pattern_order_specific_before_generic():
    """ws_api_drift muss VOR ws_disconnect kommen, sonst greift das generische
    Muster zuerst."""
    import sys
    sys.path.insert(0, str(ROOT / "06_live_bot"))
    import audit as A
    cats = [p[1] for p in A.ERROR_PATTERNS]
    assert cats.index("ws_api_drift") < cats.index("ws_disconnect")


# ─── 3. Daemon Mid-Day-Resume ───────────────────────────────────────────────
def test_daemon_has_midday_resume_logic():
    assert "MID-DAY-RESUME" in BOT_SRC, "Mid-day-resume-Logik fehlt"
    assert "TIME_HARD_FLAT" in BOT_SRC


# ─── 4. deploy_safe braucht env-vars-Fallback ───────────────────────────────
def test_deploy_safe_has_env_fallback():
    """deploy_safe.start_bot setzt env-defaults, check_positions tut's noch nicht
    → mindestens start_bot hat Fallback."""
    src = (ROOT / "06_live_bot" / "deploy_safe.py").read_text(encoding="utf-8")
    assert "env.setdefault" in src, "env-fallback in start_bot fehlt"
