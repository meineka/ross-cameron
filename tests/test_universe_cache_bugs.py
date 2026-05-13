"""Audit-Iter 25 (2026-05-12): fetch_us_universe resilience.

Bugs:
  UV-2 (HIGH): kein Retry pro URL. Single HTTP failure → URL skipped.
  UV-4 (MED): kein Cache → 2 HTTP-Requests pro premarket_scan + intraday_rescan.
    Mit 5-min rescan = 24 reqs/h → unnötig NASDAQ-Server-Last.
  UV-9 (HIGH): kein Fallback wenn ALLE URLs failen. Bot trades nichts an
    NASDAQ-Trader-CSV-Outage-Day, obwohl gestern's universe noch gut wäre.
  UV-8 (LOW): kein User-Agent header → manche Server blocken default UA.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Per-test cache file."""
    import bot
    cache = tmp_path / "universe_cache.json"
    monkeypatch.setattr(bot, "_UNIVERSE_CACHE_FILE", cache)
    yield


# ─── Bug UV-4: caching ───────────────────────────────────────────────────────
def test_fresh_cache_hit_skips_http():
    """Cache age < TTL → kein HTTP-Call."""
    import bot
    # Seed cache mit fresh ts
    bot._save_cached_universe(["AAPL", "TSLA", "MSFT"])
    with patch("requests.get") as mock_get:
        result = bot.fetch_us_universe()
    mock_get.assert_not_called()
    assert "AAPL" in result


def test_stale_cache_triggers_http():
    """Cache age > TTL → re-fetch."""
    import bot
    # Seed cache mit OLD ts
    cache = bot._UNIVERSE_CACHE_FILE
    cache.write_text(
        json.dumps({"ts": time.time() - 24*3600, "tickers": ["OLD"]}),
        encoding="utf-8",
    )
    # Mock successful HTTP
    fake_csv = "Symbol|Test Issue|ETF\nAAPL|N|N\nNEW|N|N\n"
    response = MagicMock()
    response.text = fake_csv
    response.raise_for_status = MagicMock()
    with patch("requests.get", return_value=response):
        result = bot.fetch_us_universe()
    # Wurde HTTP gerufen?
    assert "NEW" in result or "AAPL" in result
    assert "OLD" not in result  # alte Liste wurde überschrieben


# ─── Bug UV-9: stale-cache fallback ──────────────────────────────────────────
def test_fallback_to_stale_cache_when_all_urls_fail():
    """Beide URLs raisen → stale cache wird returnt."""
    import bot
    # Seed mit STALE cache (age > TTL)
    cache = bot._UNIVERSE_CACHE_FILE
    cache.write_text(
        json.dumps({"ts": time.time() - 24*3600, "tickers": ["STALE1", "STALE2"]}),
        encoding="utf-8",
    )
    with patch("requests.get", side_effect=RuntimeError("network down")):
        with patch("time.sleep"):  # skip retry-waits
            result = bot.fetch_us_universe()
    # Fallback aktiviert → stale tickers
    assert "STALE1" in result
    assert "STALE2" in result


def test_no_fallback_when_use_cache_false():
    """use_cache=False → kein stale-fallback, return empty."""
    import bot
    cache = bot._UNIVERSE_CACHE_FILE
    cache.write_text(
        json.dumps({"ts": time.time() - 24*3600, "tickers": ["STALE"]}),
        encoding="utf-8",
    )
    with patch("requests.get", side_effect=RuntimeError("down")):
        with patch("time.sleep"):
            result = bot.fetch_us_universe(use_cache=False)
    assert result == []


def test_empty_universe_without_cache_returns_empty():
    """Keine cache vorhanden + alle URLs failen → []."""
    import bot
    # Cache existiert nicht (fresh fixture)
    with patch("requests.get", side_effect=RuntimeError("down")):
        with patch("time.sleep"):
            result = bot.fetch_us_universe()
    assert result == []


# ─── Bug UV-2: retry per URL ─────────────────────────────────────────────────
def test_retries_each_url_on_failure():
    """max_retries=2 → 3 attempts pro URL (1 + 2 retries)."""
    import bot
    call_count = {"n": 0}
    fake_csv = "Symbol|Test Issue|ETF\nAAPL|N|N\n"

    def maybe_succeed(url, **kw):
        call_count["n"] += 1
        # 2 URLs * 3 attempts each, succeed on the 3rd attempt
        if call_count["n"] % 3 != 0:
            raise RuntimeError("flaky")
        response = MagicMock()
        response.text = fake_csv
        response.raise_for_status = MagicMock()
        return response

    with patch("requests.get", side_effect=maybe_succeed):
        with patch("time.sleep"):
            result = bot.fetch_us_universe(max_retries=2)
    # 2 URLs * 3 = 6 attempts total
    assert call_count["n"] == 6
    assert "AAPL" in result


def test_succeeds_on_first_attempt_per_url():
    """Beide URLs succeeden first-try → 2 calls total."""
    import bot
    call_count = {"n": 0}
    fake_csv = "Symbol|Test Issue|ETF\nAAPL|N|N\n"

    def succeed(url, **kw):
        call_count["n"] += 1
        response = MagicMock()
        response.text = fake_csv
        response.raise_for_status = MagicMock()
        return response

    with patch("requests.get", side_effect=succeed):
        result = bot.fetch_us_universe(max_retries=2)
    # 2 URLs, je 1 attempt
    assert call_count["n"] == 2


# ─── Bug UV-8: User-Agent ────────────────────────────────────────────────────
def test_sends_user_agent_header():
    """User-Agent header gesetzt — manche Server blocken default UA."""
    import bot
    captured = {"headers": None}
    fake_csv = "Symbol|Test Issue|ETF\nAAPL|N|N\n"

    def capture(url, **kw):
        captured["headers"] = kw.get("headers")
        response = MagicMock()
        response.text = fake_csv
        response.raise_for_status = MagicMock()
        return response

    with patch("requests.get", side_effect=capture):
        bot.fetch_us_universe(use_cache=False)
    assert captured["headers"] is not None
    assert "User-Agent" in captured["headers"]


# ─── Bug UV-3: HTTP-Status check ─────────────────────────────────────────────
def test_raises_on_404_status(caplog):
    """404-Response → raise_for_status() → caught + logged."""
    import bot
    import logging

    def http_404(url, **kw):
        response = MagicMock()
        response.text = "<html>Not Found</html>"
        response.raise_for_status.side_effect = RuntimeError("404 Not Found")
        return response

    with patch("requests.get", side_effect=http_404):
        with patch("time.sleep"):
            with caplog.at_level(logging.WARNING):
                result = bot.fetch_us_universe(use_cache=False)
    assert result == []
    assert any("attempt" in r.message for r in caplog.records)


def test_empty_response_logged_as_failure(caplog):
    """Server returns empty body → logged."""
    import bot
    import logging

    def empty(url, **kw):
        response = MagicMock()
        response.text = ""
        response.raise_for_status = MagicMock()
        return response

    with patch("requests.get", side_effect=empty):
        with patch("time.sleep"):
            with caplog.at_level(logging.WARNING):
                result = bot.fetch_us_universe(use_cache=False)
    assert result == []
    assert any("empty" in r.message.lower() for r in caplog.records)


# ─── Cache durability ────────────────────────────────────────────────────────
def test_corrupt_cache_treated_as_no_cache():
    """Corrupt JSON in cache file → treat as no cache."""
    import bot
    bot._UNIVERSE_CACHE_FILE.write_text("garbage {not json}", encoding="utf-8")
    cached, age = bot._load_cached_universe()
    assert cached is None


def test_cache_atomic_write():
    """_save_cached_universe schreibt zu .tmp + rename."""
    import bot
    import os
    original_replace = os.replace
    replace_calls = []

    def spy(src, dst):
        replace_calls.append((src, dst))
        original_replace(src, dst)

    with patch("os.replace", side_effect=spy):
        bot._save_cached_universe(["A", "B"])
    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert src.endswith(".json.tmp")
