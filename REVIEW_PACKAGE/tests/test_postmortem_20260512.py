"""Post-Mortem-Tests für 2026-05-12 Findings.

Was an dem Tag aufgefallen war:
  1. ~3000 yfinance-delisted-ERROR-Logs verursachten Monitor-Spam und stoppten
     den Trading-Events-Monitor automatisch.
  2. Audit-Pattern matched einige yfinance-Variants nicht (`['SYM']:`, leere
     `ERROR [yfinance]`-Zeilen) → fielen als 'unknown=high' durch → Audit-ALARM.
  3. Threshold für INVESTIGATE_HIGH_SEVERITY war 3 — bei yfinance-Spam zu niedrig.
  4. Premarket-Scan tried jeden Tag die gleichen dead tickers neu (kein Cache).
"""
from __future__ import annotations
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. yfinance-Logging dämpfen ─────────────────────────────────────────────
def test_bot_silences_yfinance_logger():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert 'getLogger("yfinance")' in src
    assert "logging.CRITICAL" in src


# ─── 2. Audit-Pattern erweitert ──────────────────────────────────────────────
def _classify(line: str):
    import audit as A
    for pattern, category, severity, fixable, hint in A.ERROR_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return category, severity, fixable
    return "unknown", "high", False


@pytest.mark.parametrize("line", [
    "2026-05-12 12:28:45,433 ERROR [yfinance] $EYPT: possibly delisted; no price data found",
    "2026-05-12 12:31:25,263 ERROR [yfinance] ['UCFIW']: possibly delisted; no price data found",
    "2026-05-12 13:04:38,731 ERROR [yfinance] ['NERV']: YFRateLimitError('Too Many Requests. Rate limited.')",
])
def test_audit_classifies_yfinance_variants_as_info(line):
    cat, sev, _ = _classify(line)
    assert cat in ("yfinance_delisted", "yfinance_rate_limit"), f"unhandled: {line!r}"
    assert sev in ("info", "low"), f"severity should be info/low, got {sev}"


def test_audit_classifies_empty_yfinance_line():
    line = "2026-05-12 12:31:25,263 ERROR [yfinance]"
    cat, sev, _ = _classify(line)
    assert cat in ("yfinance_delisted", "yfinance_empty_line")
    assert sev == "info"


# ─── 3. Threshold raised ─────────────────────────────────────────────────────
def test_audit_high_severity_threshold_raised():
    src = (ROOT / "06_live_bot" / "audit.py").read_text(encoding="utf-8")
    assert 'high_severity_errors"] > 10' in src or 'high_severity_errors"] >= 10' in src
    assert 'high_severity_errors"] > 3' not in src


# ─── 4. Delisted-Cache ───────────────────────────────────────────────────────
def test_delisted_cache_roundtrip(tmp_path, monkeypatch):
    import delisted_cache
    monkeypatch.setattr(delisted_cache, "CACHE_FILE", tmp_path / "dc.json")
    delisted_cache._cache = None
    assert delisted_cache.is_delisted("AAA") is False
    delisted_cache.mark_delisted("AAA")
    assert delisted_cache.is_delisted("AAA") is True


def test_delisted_cache_expires(tmp_path, monkeypatch):
    import delisted_cache
    monkeypatch.setattr(delisted_cache, "CACHE_FILE", tmp_path / "dc.json")
    delisted_cache._cache = None
    # Simuliere alten Eintrag (40 Tage)
    delisted_cache._cache = {"OLD": time.time() - 40 * 86400}
    delisted_cache._save()
    delisted_cache._cache = None
    assert delisted_cache.is_delisted("OLD") is False  # expired


def test_delisted_cache_filter_batch(tmp_path, monkeypatch):
    import delisted_cache
    monkeypatch.setattr(delisted_cache, "CACHE_FILE", tmp_path / "dc.json")
    delisted_cache._cache = None
    delisted_cache.mark_batch_delisted(["DEAD1", "DEAD2"])
    alive, skipped = delisted_cache.filter_known_delisted(["DEAD1", "ALIVE", "DEAD2", "FRESH"])
    assert "DEAD1" not in alive
    assert "DEAD2" not in alive
    assert "ALIVE" in alive
    assert "FRESH" in alive
    assert skipped == 2


def test_bot_uses_delisted_cache_in_scan():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "filter_known_delisted" in src
    assert "mark_batch_delisted" in src


# ─── 5. Smoke: audit.py imports + classify works ─────────────────────────────
def test_audit_imports_clean():
    import audit
    assert hasattr(audit, "ERROR_PATTERNS")
    assert hasattr(audit, "classify_errors")
