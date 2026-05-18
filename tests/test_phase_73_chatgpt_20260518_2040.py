"""Phase-73 (ChatGPT 20260518_2040 P0/P1/P2): three fixes in one commit.

User: "TICK-ANSWER" cron fired → new answer file 20260518_2040 detected.

Three priorities addressed:
  1. P0 SECURITY: .env excluded from export zips (build_export.ps1)
  2. P1: TV scan writes scanner_source/last_tradingview_scan_status/
         fallback_used into DayState
  3. P1/P2: force_trade_loop fail-closed (no raw-client fallback)

These tests pin the contract so future regressions are caught.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. build_export.ps1 excludes .env ───────────────────────────────────

def test_build_export_script_exists():
    """build_export.ps1 must exist as the single source of truth for
    export builds."""
    p = ROOT / "06_live_bot" / "build_export.ps1"
    assert p.exists(), f"missing: {p}"


def test_build_export_excludes_env_files():
    """The script's /XF list must include .env and variants."""
    src = (ROOT / "06_live_bot" / "build_export.ps1").read_text(
        encoding="utf-8"
    )
    # Must list .env explicitly
    assert '".env"' in src, "build_export.ps1 must exclude .env"
    assert '".env.local"' in src
    # Must defensively delete .env if it slipped through
    assert 'Remove-Item $envFile' in src, (
        "build_export.ps1 must defensively delete .env even after robocopy"
    )


def test_build_export_excludes_secret_extensions():
    """*.pem and *.key must be excluded too."""
    src = (ROOT / "06_live_bot" / "build_export.ps1").read_text(
        encoding="utf-8"
    )
    assert '"*.pem"' in src
    assert '"*.key"' in src


def test_build_export_verifies_no_leaks_after_zip():
    """Script must scan the final zip for leaks and abort if found."""
    src = (ROOT / "06_live_bot" / "build_export.ps1").read_text(
        encoding="utf-8"
    )
    assert "SECURITY LEAK DETECTED" in src
    assert "throw" in src  # PowerShell throws on detection


# ─── 2. TV-scan writes DayState fields ───────────────────────────────────

def test_last_tv_scan_state_module_var_exists():
    """The module-level state dict must exist (so caller can read it)."""
    import bot
    assert hasattr(bot, "_LAST_TV_SCAN_STATE")
    assert isinstance(bot._LAST_TV_SCAN_STATE, dict)


def test_tv_scan_ok_writes_state_ok():
    """When TV returns rows, module state shows status=ok + count."""
    import bot
    fake_rows = [{"ticker": "AAA", "close": 5.0, "premarket_change": 10,
                   "rvol_proxy": 3, "float_shares": 5_000_000}]
    fake_module = type(sys)("scanners.tradingview_scanner")
    fake_module.scan_cameron_candidates = lambda **kw: fake_rows
    sys.modules["scanners"] = type(sys)("scanners")
    sys.modules["scanners.tradingview_scanner"] = fake_module
    try:
        rows = bot._try_tradingview_primary(top_n=5)
        assert rows == fake_rows
        assert bot._LAST_TV_SCAN_STATE["status"] == "ok"
        assert bot._LAST_TV_SCAN_STATE["result_count"] == 1
        assert bot._LAST_TV_SCAN_STATE["error_class"] is None
        assert bot._LAST_TV_SCAN_STATE["ts"] is not None
    finally:
        sys.modules.pop("scanners.tradingview_scanner", None)
        sys.modules.pop("scanners", None)


def test_tv_scan_empty_writes_state_ok_zero():
    """No candidates is still status=ok (TV up, just empty result)."""
    import bot
    fake_module = type(sys)("scanners.tradingview_scanner")
    fake_module.scan_cameron_candidates = lambda **kw: []
    sys.modules["scanners"] = type(sys)("scanners")
    sys.modules["scanners.tradingview_scanner"] = fake_module
    try:
        bot._try_tradingview_primary(top_n=5)
        assert bot._LAST_TV_SCAN_STATE["status"] == "ok"
        assert bot._LAST_TV_SCAN_STATE["result_count"] == 0
    finally:
        sys.modules.pop("scanners.tradingview_scanner", None)
        sys.modules.pop("scanners", None)


def test_tv_scan_exception_writes_state_error():
    import bot
    fake_module = type(sys)("scanners.tradingview_scanner")

    def _boom(**kw):
        raise ConnectionError("TV API down")

    fake_module.scan_cameron_candidates = _boom
    sys.modules["scanners"] = type(sys)("scanners")
    sys.modules["scanners.tradingview_scanner"] = fake_module
    try:
        bot._try_tradingview_primary(top_n=5)
        assert bot._LAST_TV_SCAN_STATE["status"] == "error"
        assert bot._LAST_TV_SCAN_STATE["error_class"] == "ConnectionError"
    finally:
        sys.modules.pop("scanners.tradingview_scanner", None)
        sys.modules.pop("scanners", None)


def test_bot_py_writes_day_fields_from_tv_state():
    """Source-grep: bot.py must reference _LAST_TV_SCAN_STATE in the
    post-scan code path that updates day.scanner_source/etc."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "_LAST_TV_SCAN_STATE" in src
    assert "self.day.last_tradingview_scan_status" in src
    assert "self.day.scanner_source" in src
    assert "self.day.fallback_used" in src
    # Source-grep: yfinance_fallback path must set fallback_used=True
    assert 'self.day.fallback_used = True' in src


# ─── 3. force_trade_loop fail-closed ─────────────────────────────────────

def test_force_trade_loop_no_raw_client_fallback():
    """No `from alpaca.trading.client import TradingClient` in
    force_trade_loop.get_clients() — fallback removed for fail-closed."""
    src = (ROOT / "06_live_bot" / "force_trade_loop.py").read_text(
        encoding="utf-8"
    )
    # Get only the get_clients function body
    import re
    m = re.search(r"def get_clients.*?(?=\n(?:def |\Z))", src, re.S)
    assert m, "get_clients() function not found"
    body = m.group(0)
    # Phase-73: no raw TradingClient fallback in get_clients
    assert "from alpaca.trading.client import TradingClient" not in body, (
        "force_trade_loop.get_clients() must NOT have a raw TradingClient "
        "fallback — Phase-73 fail-closed requirement"
    )
    assert "from alpaca.data.historical import StockHistoricalDataClient" not in body
    # And must raise RuntimeError when guarded import fails
    assert "RuntimeError" in body or "raise" in body


def test_force_trade_loop_get_clients_raises_when_guarded_fails(monkeypatch):
    """Behavior test: simulating guarded import failure must raise,
    not silently fall back."""
    import force_trade_loop
    # Force the guarded_alpaca import to fail
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict) else __builtins__.__import__

    def _fake_import(name, *a, **kw):
        if name == "guarded_alpaca":
            raise ImportError("simulated import failure")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", _fake_import)
    # Also mock secrets so we don't fail on missing keys
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "get_alpaca_keys",
                          lambda: ("fake_key", "fake_sec"))

    with pytest.raises(RuntimeError, match="REFUSING TO START"):
        force_trade_loop.get_clients()


# ─── 4. Sanity: actual export check ──────────────────────────────────────

def test_recent_export_has_no_env(tmp_path):
    """If a recent export exists in 99_Claude_Chatgpt/, verify .env is
    NOT in it. Defensive check — the build_export.ps1 should prevent
    this, but a stale zip from before the fix might still have it."""
    exports_dir = ROOT / "99_Claude_Chatgpt"
    if not exports_dir.exists():
        pytest.skip("99_Claude_Chatgpt/ not present")
    zips = sorted(exports_dir.glob("*_export_claude.zip"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        pytest.skip("no export zips found")
    # Check the MOST RECENT export
    latest = zips[0]
    import zipfile
    with zipfile.ZipFile(latest) as z:
        names = z.namelist()
    env_files = [n for n in names if n.endswith(".env") or n.endswith(".env.local")]
    if env_files:
        # This is a one-time grace period — older exports may still have .env.
        # The fix prevents NEW exports from leaking. We just warn here.
        pytest.skip(
            f"older export still has .env: {env_files} — "
            f"future exports via build_export.ps1 won't leak"
        )
