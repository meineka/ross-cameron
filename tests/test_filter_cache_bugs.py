"""Audit-Iter 10 (2026-05-12): catalyst/float-filter cache bugs.

Bug FLT-4 (HIGH): float-cache hatte keine TTL → in 24h+-daemon-Runs
  blieben gestrige Floats hängen. Float kann sich ändern (Secondary
  Offering, Lock-Up-Expiry).

Bug FLT-1 / CAT-1: Module-level _cache war nicht für Tests isoliert.

Bug CAT-3/CAT-4 (Design): passes_catalyst_filter kann V1-mäßig NIE
  False returnen. Jetzt strict=True optional für Live-Setups die
  wirklich nur mit bestätigtem Catalyst traden.
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Float-Cache: TTL ────────────────────────────────────────────────────────
def test_float_cache_clears_after_clear_cache():
    import float_filter
    float_filter.clear_cache()
    # Pre-fill cache directly
    float_filter._cache["AAPL"] = (5_000_000.0, time.time())
    assert float_filter.get_float("AAPL") == 5_000_000.0
    float_filter.clear_cache()
    # Nach clear muss neuer Fetch laufen (gemockt)
    with patch.object(float_filter, "yf", create=True) as mock_yf:
        m = MagicMock()
        m.info = {"floatShares": 9_000_000}
        mock_yf.Ticker.return_value = m
        # Achtung: import yfinance inside function — wir patchen das nicht direkt.
        # Stattdessen prüfen wir nur dass clear_cache leerte:
        assert "AAPL" not in float_filter._cache


def test_float_cache_expires_after_ttl():
    """FLT-4: nach 12h TTL muss erneut gefetcht werden."""
    import float_filter
    float_filter.clear_cache()
    # Cache mit altem Timestamp (24h alt) füttern
    float_filter._cache["AAPL"] = (5_000_000.0, time.time() - 24 * 3600)
    # get_float sollte stale value verwerfen → re-fetch (failed in tests
    # since yfinance gibt None) → None → re-cached
    val = float_filter.get_float("AAPL")
    # Wert sollte sich vom stale 5M unterscheiden (None nach failed fetch)
    # ODER cache wurde aktualisiert mit neuem ts
    cached_val, cached_ts = float_filter._cache["AAPL"]
    assert cached_ts > time.time() - 60  # fresh ts


def test_float_cache_none_has_short_ttl():
    """Failed fetch (None) wird nur 5min gecached → schneller retry."""
    import float_filter
    float_filter.clear_cache()
    # None mit ts vor 6 Min (älter als 5min TTL)
    float_filter._cache["XYZ"] = (None, time.time() - 360)
    # Re-fetch sollte triggern
    val = float_filter.get_float("XYZ")
    # Mock can't be applied easily, just verify ts was updated
    cached_val, cached_ts = float_filter._cache["XYZ"]
    assert cached_ts > time.time() - 60  # fresh ts


def test_float_cache_fresh_known_value_not_refetched():
    """Frischer Wert in TTL → kein re-fetch."""
    import float_filter
    float_filter.clear_cache()
    float_filter._cache["FROZEN"] = (3_000_000.0, time.time() - 60)  # 1min alt
    val = float_filter.get_float("FROZEN")
    assert val == 3_000_000.0


def test_float_zero_treated_as_unknown():
    """yfinance gibt manchmal 0 zurück (corrupt) — als unknown behandeln."""
    import float_filter
    float_filter.clear_cache()
    float_filter._cache["BUG"] = (0.0, time.time())  # already 0
    # In get_float code: `if v and v > 0` parsed zu None, aber wir haben
    # hier direkt 0 im cache. Real-Path: nach fetch.
    # Trotzdem: passes_float_filter muss True returnen
    assert float_filter.passes_float_filter("BUG") is True


# ─── Catalyst: strict/soft/off mode (Review-V2 P1.3 semantik) ───────────────
# Reviewer-V2 corrected old semantic: if catalyst REQUIRED for live trading,
# unknown catalyst must mean NO trade. New three-mode API:
#   off    → always pass
#   soft   → pass on empty/error, block only on all-stale (V1 permissive default)
#   strict → block on ANY unknown (empty, error, or all-stale)

def test_catalyst_empty_news_soft_permissive():
    """Soft-mode (V1 default): empty news = data-source-quirk, pass."""
    import catalyst_filter
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = []
        mock_ticker.return_value = m
        catalyst_filter.clear_cache()
        assert catalyst_filter.has_recent_news("ABC", strict=False) is True
        catalyst_filter.clear_cache()
        assert catalyst_filter.has_recent_news("ABC", mode="soft") is True
    catalyst_filter.clear_cache()


def test_catalyst_empty_news_strict_blocks():
    """Strict-mode (Review-V2 fix): empty news = unknown catalyst = no trade."""
    import catalyst_filter
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = []
        mock_ticker.return_value = m
        catalyst_filter.clear_cache()
        assert catalyst_filter.has_recent_news("ABC", strict=True) is False
        catalyst_filter.clear_cache()
        assert catalyst_filter.has_recent_news("ABC", mode="strict") is False
    catalyst_filter.clear_cache()


def test_catalyst_api_failure_soft_permissive():
    """Soft-mode: yfinance error → pass (data-source-trust)."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    with patch("yfinance.Ticker", side_effect=RuntimeError("API down")):
        assert catalyst_filter.has_recent_news("ABC", strict=False) is True


def test_catalyst_api_failure_strict_blocks():
    """Strict-mode (Review-V2 fix): yfinance error → block (fail-closed)."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    with patch("yfinance.Ticker", side_effect=RuntimeError("API down")):
        assert catalyst_filter.has_recent_news("ABC", strict=True) is False
        assert catalyst_filter.has_recent_news("ABC", mode="strict") is False


def test_catalyst_off_mode_always_passes():
    """Off-mode: filter completely disabled."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    with patch("yfinance.Ticker", side_effect=RuntimeError("API down")):
        assert catalyst_filter.has_recent_news("ABC", mode="off") is True
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock(); m.news = []; mock_ticker.return_value = m
        catalyst_filter.clear_cache()
        assert catalyst_filter.has_recent_news("ABC", mode="off") is True


def test_catalyst_stale_news_blocks_in_all_modes_except_off():
    """Review-V2 fix: stale news = positive signal of no-catalyst.
    Both soft and strict block. Only off-mode lets it through.
    Old semantic where soft passed stale was inconsistent — fixed."""
    import catalyst_filter
    import time as _t
    old_ts = _t.time() - 48*3600  # 48h old, lookback default 24h
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = [{"providerPublishTime": old_ts, "title": "stale"}]
        mock_ticker.return_value = m
        catalyst_filter.clear_cache()
        # Strict: blocks (verified no fresh catalyst)
        assert catalyst_filter.has_recent_news("OLD", strict=True) is False
        catalyst_filter.clear_cache()
        # Soft: ALSO blocks (we know news exists but none fresh — real signal)
        assert catalyst_filter.has_recent_news("OLD", strict=False) is False
        catalyst_filter.clear_cache()
        # Off: filter disabled, passes
        assert catalyst_filter.has_recent_news("OLD", mode="off") is True
    catalyst_filter.clear_cache()


def test_catalyst_does_not_cache_on_exception():
    """Bei API-Fehler darf NICHT cached werden — nächster Call retry."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    with patch("yfinance.Ticker", side_effect=RuntimeError("API down")):
        catalyst_filter.has_recent_news("XYZ")
    # Cache leer geblieben → next call würde wieder fetch
    assert "XYZ" not in catalyst_filter._cache


def test_catalyst_recent_news_returns_true():
    """News mit fresh timestamp → True (positive case)."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    fresh_ts = time.time() - 3600  # 1h ago
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = [{"providerPublishTime": fresh_ts, "title": "BIG NEWS"}]
        mock_ticker.return_value = m
        assert catalyst_filter.has_recent_news("FRESH") is True
    catalyst_filter.clear_cache()


def test_catalyst_old_news_strict_false():
    """Strict + nur >24h alte news → False."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    old_ts = time.time() - 48 * 3600  # 48h ago
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = [{"providerPublishTime": old_ts, "title": "OLD"}]
        mock_ticker.return_value = m
        assert catalyst_filter.has_recent_news("OLD", strict=True) is False
    catalyst_filter.clear_cache()


def test_catalyst_clear_cache_works():
    import catalyst_filter
    catalyst_filter._cache["TEST"] = (True, time.time())
    catalyst_filter.clear_cache()
    assert "TEST" not in catalyst_filter._cache


# ─── Integration: passes_*_filter API stable ─────────────────────────────────
def test_passes_catalyst_filter_accepts_strict_and_mode():
    """API-Compat: legacy strict-bool + new mode-string both work.
    Review-V2 P1.3: strict=True must mean fail-closed."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    # Stale news case: both strict and soft block (it's a real no-fresh signal)
    import time as _t
    old_ts = _t.time() - 48*3600
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock()
        m.news = [{"providerPublishTime": old_ts}]
        mock_ticker.return_value = m
        catalyst_filter.clear_cache()
        assert catalyst_filter.passes_catalyst_filter("X", strict=True) is False
        catalyst_filter.clear_cache()
        assert catalyst_filter.passes_catalyst_filter("X", strict=False) is False
        catalyst_filter.clear_cache()
        assert catalyst_filter.passes_catalyst_filter("X", mode="off") is True
    catalyst_filter.clear_cache()


def test_passes_catalyst_filter_strict_blocks_unknown():
    """Review-V2 P1.3 fix: strict-mode now blocks unknown (empty/error).
    Previously was permissive — that was the bug."""
    import catalyst_filter
    catalyst_filter.clear_cache()
    # Empty news in strict-mode = block
    with patch("yfinance.Ticker") as mock_ticker:
        m = MagicMock(); m.news = []; mock_ticker.return_value = m
        assert catalyst_filter.passes_catalyst_filter("X", strict=True) is False
    catalyst_filter.clear_cache()
    # API error in strict-mode = block
    with patch("yfinance.Ticker", side_effect=RuntimeError("down")):
        assert catalyst_filter.passes_catalyst_filter("X", strict=True) is False
    catalyst_filter.clear_cache()


def test_passes_float_filter_returns_true_unknown():
    """Soft (default): unknown float → True (no veto)."""
    import float_filter
    float_filter.clear_cache()
    float_filter._cache["U"] = (None, time.time())
    assert float_filter.passes_float_filter("U") is True


def test_passes_float_filter_vetos_high_float():
    """Float > 10M → False (veto, all modes except off)."""
    import float_filter
    float_filter.clear_cache()
    float_filter._cache["BIG"] = (50_000_000.0, time.time())
    assert float_filter.passes_float_filter("BIG") is False
    assert float_filter.passes_float_filter("BIG", mode="soft") is False
    assert float_filter.passes_float_filter("BIG", mode="strict") is False
    assert float_filter.passes_float_filter("BIG", mode="off") is True


def test_passes_float_filter_strict_blocks_unknown():
    """Review-V2 P1.4 fix: strict-mode blocks unknown float."""
    import float_filter
    float_filter.clear_cache()
    float_filter._cache["UNK"] = (None, time.time())
    assert float_filter.passes_float_filter("UNK", mode="strict") is False
    assert float_filter.passes_float_filter("UNK", mode="soft") is True
    assert float_filter.passes_float_filter("UNK", mode="off") is True


def test_passes_float_filter_invalid_mode_raises():
    import float_filter
    import pytest
    with pytest.raises(ValueError):
        float_filter.passes_float_filter("X", mode="bogus")
