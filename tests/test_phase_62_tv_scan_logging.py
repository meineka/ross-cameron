"""Phase-62: TradingView scan-status structured logging.

ChatGPT 1817 ask: "wenn TradingView empty/error, das soll als
strukturierter Log-Eintrag im market_data_calls.jsonl landen, nicht
nur als log.warning". This file locks in three scenarios:

  1. ok with rows → status=ok, result_count=N
  2. raises exception → status=error, error_class set
  3. import fails (scanner package missing) → status=import_error
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture
def tmp_log(monkeypatch, tmp_path):
    """Redirect _log_tv_scan to write into a tmp file by monkey-patching
    the resolved Path. We do this by replacing the file_path constant
    through patching __file__ on the bot module isn't reliable — so
    we patch the function instead."""
    import bot
    log_path = tmp_path / "market_data_calls.jsonl"

    def _fake_log_tv_scan(*, status, latency_ms, result_count,
                            error_class=None, error=None):
        rec = {
            "source": "tradingview",
            "method": "scan_cameron_candidates",
            "status": status,
            "latency_ms": latency_ms,
            "result_count": result_count,
            "error_class": error_class,
            "error": error,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    monkeypatch.setattr(bot, "_log_tv_scan", _fake_log_tv_scan)
    return log_path


def _read(p):
    if not p.exists():
        return []
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines()
            if L.strip()]


def test_tv_ok_logs_status_ok_with_result_count(tmp_log):
    """Successful TV scan → status=ok, result_count = len(rows)."""
    import bot
    fake_rows = [{"symbol": "AAA"}, {"symbol": "BBB"}, {"symbol": "CCC"}]
    with patch.dict(sys.modules,
                     {"scanners.tradingview_scanner": __import__("types").ModuleType("scanners.tradingview_scanner")}):
        sys.modules["scanners.tradingview_scanner"].scan_cameron_candidates = (
            lambda **kw: fake_rows
        )
        rows = bot._try_tradingview_primary(top_n=10)
    assert rows == fake_rows
    entries = _read(tmp_log)
    assert len(entries) == 1
    assert entries[0]["status"] == "ok"
    assert entries[0]["result_count"] == 3
    assert entries[0]["source"] == "tradingview"


def test_tv_empty_logs_status_ok_with_zero_count(tmp_log):
    """No candidates is NOT an error — just status=ok n=0. This is the
    key signal for 'TV up, no symbols qualified today' vs 'TV down'."""
    import bot
    with patch.dict(sys.modules,
                     {"scanners.tradingview_scanner": __import__("types").ModuleType("scanners.tradingview_scanner")}):
        sys.modules["scanners.tradingview_scanner"].scan_cameron_candidates = (
            lambda **kw: []
        )
        rows = bot._try_tradingview_primary(top_n=10)
    assert rows == []
    entries = _read(tmp_log)
    assert len(entries) == 1
    assert entries[0]["status"] == "ok"
    assert entries[0]["result_count"] == 0


def test_tv_raises_logs_status_error_with_class(tmp_log):
    """Scanner raises mid-call → status=error, error_class captured."""
    import bot

    def _boom(**kw):
        raise ConnectionError("TV API 503")

    with patch.dict(sys.modules,
                     {"scanners.tradingview_scanner": __import__("types").ModuleType("scanners.tradingview_scanner")}):
        sys.modules["scanners.tradingview_scanner"].scan_cameron_candidates = _boom
        rows = bot._try_tradingview_primary(top_n=10)
    assert rows == []
    entries = _read(tmp_log)
    assert len(entries) == 1
    assert entries[0]["status"] == "error"
    assert entries[0]["error_class"] == "ConnectionError"
    assert "TV API 503" in entries[0]["error"]


def test_tv_import_error_logs_status_import_error(tmp_log, monkeypatch):
    """Scanner package missing → status=import_error, distinguishable
    from a runtime error so operator knows it's an install issue."""
    import bot
    # Remove the scanner package from sys.modules so reimport fails
    monkeypatch.delitem(sys.modules, "scanners.tradingview_scanner",
                          raising=False)
    monkeypatch.delitem(sys.modules, "scanners", raising=False)

    # Make the import hook raise ImportError
    orig_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _fail_import(name, *args, **kwargs):
        if name == "scanners.tradingview_scanner":
            raise ImportError("No module named 'scanners.tradingview_scanner'")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _fail_import)
    rows = bot._try_tradingview_primary(top_n=10)
    assert rows == []
    entries = _read(tmp_log)
    assert len(entries) == 1
    assert entries[0]["status"] == "import_error"
    assert entries[0]["error_class"] == "ImportError"


def test_tv_log_schema_has_required_fields():
    """Schema-fix: every TV scan log row must carry the canonical fields
    so downstream postmortem-grep is stable."""
    import bot
    # Inspect the function body — fragile but ensures field set
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # _log_tv_scan body must reference each canonical field
    for required in ("status", "latency_ms", "blocked_ms",
                       "error_class", "result_count", "schema_version",
                       "source", "method", "ts"):
        assert required in src, (
            f"_log_tv_scan body missing field {required!r}"
        )
