"""Phase-63: float-cache build pipeline.

Float values gate Cameron-strict's FLOAT_MAX_SHARES = 10M filter,
which is THE key smallcap definition for the strategy. Without a
reliable float source, the backtest can't even compute the candidate
universe correctly. These tests lock in the parser + cache contract
WITHOUT making any live HTTP calls (uses fixtures / monkeypatches).
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. Float-string parser ──────────────────────────────────────────────

@pytest.mark.parametrize("inp,expected", [
    ("12.34M", 12_340_000),
    ("1.23B", 1_230_000_000),
    ("500K", 500_000),
    ("5000", 5000),
    ("4.5M", 4_500_000),
    ("9.99M", 9_990_000),       # Cameron-strict edge case
    ("10.01M", 10_010_000),     # JUST over limit
    ("-", None),
    ("", None),
    (None, None),
    ("N/A", None),
    ("0", None),                # zero is not a valid float
    ("1,234,567", 1_234_567),    # comma thousand-separator
])
def test_parse_float_str_handles_finviz_formats(inp, expected):
    from build_float_cache import _parse_float_str
    assert _parse_float_str(inp) == expected


# ─── 2. Finviz HTML extractor ────────────────────────────────────────────

FINVIZ_FIXTURE_WITH_FLOAT = """<html><body>
<table>
<tr>
<td class="snapshot-td2-cp">Index</td><td class="snapshot-td2">S&amp;P 500</td>
<td class="snapshot-td2-cp">P/E</td><td class="snapshot-td2">35.2</td>
<td class="snapshot-td2-cp">EPS (ttm)</td><td class="snapshot-td2">6.43</td>
<td class="snapshot-td2-cp">Shs Outstand</td><td class="snapshot-td2">15.32B</td>
<td class="snapshot-td2-cp">Shs Float</td><td class="snapshot-td2">8.45M</td>
<td class="snapshot-td2-cp">Volume</td><td class="snapshot-td2">52,341,234</td>
</tr>
</table>
</body></html>"""

FINVIZ_FIXTURE_NO_FLOAT = """<html><body>
<table>
<tr>
<td class="snapshot-td2-cp">Index</td><td class="snapshot-td2">-</td>
<td class="snapshot-td2-cp">P/E</td><td class="snapshot-td2">-</td>
</tr>
</table>
</body></html>"""

FINVIZ_FIXTURE_QUOTE_NOT_FOUND = "<html><body>Quote not found</body></html>"


def test_finviz_extractor_pulls_8_45m_float():
    """Headline case: Finviz snapshot table with 'Shs Float' = '8.45M'
    must produce 8_450_000 (well under 10M → smallcap)."""
    from build_float_cache import _extract_finviz_float
    assert _extract_finviz_float(FINVIZ_FIXTURE_WITH_FLOAT) == 8_450_000


def test_finviz_extractor_returns_none_when_no_float_row():
    from build_float_cache import _extract_finviz_float
    assert _extract_finviz_float(FINVIZ_FIXTURE_NO_FLOAT) is None


# ─── 3. _finviz_lookup HTTP handling ─────────────────────────────────────

def _mock_session(*, status_code=200, body="", raises=None):
    sess = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    if raises is not None:
        sess.get.side_effect = raises
    else:
        sess.get.return_value = resp
    return sess


def test_finviz_lookup_success_returns_float_and_no_error():
    from build_float_cache import _finviz_lookup
    sess = _mock_session(status_code=200, body=FINVIZ_FIXTURE_WITH_FLOAT)
    rec = _finviz_lookup("AAPL", sess)
    assert rec["float_shares"] == 8_450_000
    assert rec["error"] is None
    assert rec["http_status"] == 200


def test_finviz_lookup_404_returns_finviz_404_error():
    """Symbol not on Finviz (delisted / micro-cap) → graceful None."""
    from build_float_cache import _finviz_lookup
    sess = _mock_session(status_code=404, body="")
    rec = _finviz_lookup("NOTREAL", sess)
    assert rec["float_shares"] is None
    assert rec["error"] == "finviz_404"


def test_finviz_lookup_429_returns_rate_limited_error():
    """Rate-limit means: retry later, don't cache as permanent miss."""
    from build_float_cache import _finviz_lookup
    sess = _mock_session(status_code=429, body="")
    rec = _finviz_lookup("AAPL", sess)
    assert "rate_limited" in rec["error"]


def test_finviz_lookup_200_quote_not_found_returns_no_quote():
    """Finviz answers 200 OK but body says 'Quote not found' — common
    for recent IPOs or weird tickers. Must be detected."""
    from build_float_cache import _finviz_lookup
    sess = _mock_session(status_code=200,
                          body=FINVIZ_FIXTURE_QUOTE_NOT_FOUND)
    rec = _finviz_lookup("XYZQQ", sess)
    assert rec["float_shares"] is None
    assert rec["error"] == "finviz_no_quote"


def test_finviz_lookup_network_exception_never_propagates():
    from build_float_cache import _finviz_lookup
    sess = _mock_session(raises=ConnectionError("DNS failed"))
    rec = _finviz_lookup("AAPL", sess)
    assert rec["float_shares"] is None
    assert "exception" in rec["error"]
    assert "ConnectionError" in rec["error"]


# ─── 4. lookup_one — primary + fallback chain ────────────────────────────

def test_lookup_one_returns_finviz_result_when_finviz_succeeds(monkeypatch):
    """If Finviz returns a value, yfinance is not called."""
    import build_float_cache
    monkeypatch.setattr(build_float_cache, "_finviz_lookup",
                          lambda t, s: {"float_shares": 5_000_000,
                                          "error": None,
                                          "http_status": 200})
    yf_called = [False]

    def _yf(t):
        yf_called[0] = True
        return {"float_shares": 999, "error": None}

    monkeypatch.setattr(build_float_cache, "_yfinance_lookup", _yf)
    rec = build_float_cache.lookup_one("AAPL")
    assert rec["float_shares"] == 5_000_000
    assert rec["source"] == "finviz"
    assert yf_called[0] is False


def test_lookup_one_falls_back_to_yfinance_on_finviz_404(monkeypatch):
    """The headline fallback case: Finviz misses → yfinance covers."""
    import build_float_cache
    monkeypatch.setattr(build_float_cache, "_finviz_lookup",
                          lambda t, s: {"float_shares": None,
                                          "error": "finviz_404",
                                          "http_status": 404})
    monkeypatch.setattr(build_float_cache, "_yfinance_lookup",
                          lambda t: {"float_shares": 7_500_000,
                                       "error": None})
    rec = build_float_cache.lookup_one("RECENT_IPO")
    assert rec["float_shares"] == 7_500_000
    assert rec["source"] == "yfinance"
    assert rec["error"] is None


def test_lookup_one_returns_none_when_both_sources_fail(monkeypatch):
    """Combined error string preserves both sources' reasons."""
    import build_float_cache
    monkeypatch.setattr(build_float_cache, "_finviz_lookup",
                          lambda t, s: {"float_shares": None,
                                          "error": "finviz_404",
                                          "http_status": 404})
    monkeypatch.setattr(build_float_cache, "_yfinance_lookup",
                          lambda t: {"float_shares": None,
                                       "error": "yfinance_no_float"})
    rec = build_float_cache.lookup_one("DEAD_TICKER")
    assert rec["float_shares"] is None
    assert rec["source"] == "none"
    assert "finviz_404" in rec["error"]
    assert "yfinance_no_float" in rec["error"]


def test_lookup_one_can_disable_yfinance_fallback(monkeypatch):
    """--no-yfinance flag: don't even call yfinance when Finviz misses."""
    import build_float_cache
    monkeypatch.setattr(build_float_cache, "_finviz_lookup",
                          lambda t, s: {"float_shares": None,
                                          "error": "finviz_404",
                                          "http_status": 404})
    yf_called = [False]

    def _yf(t):
        yf_called[0] = True
        return {"float_shares": 1, "error": None}

    monkeypatch.setattr(build_float_cache, "_yfinance_lookup", _yf)
    rec = build_float_cache.lookup_one("X",
                                          use_yfinance_fallback=False)
    assert rec["float_shares"] is None
    assert yf_called[0] is False


# ─── 5. Cache freshness logic ────────────────────────────────────────────

def test_is_stale_returns_true_for_empty_record():
    from build_float_cache import _is_stale
    assert _is_stale({}, max_age_days=7) is True


def test_is_stale_returns_false_for_recent_record():
    from build_float_cache import _is_stale
    rec = {
        "float_shares": 5_000_000,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
    }
    assert _is_stale(rec, max_age_days=7) is False


def test_is_stale_returns_true_for_old_record():
    from build_float_cache import _is_stale
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    rec = {"float_shares": 5_000_000, "fetched_at": old_ts, "error": None}
    assert _is_stale(rec, max_age_days=7) is True


def test_is_stale_returns_true_for_rate_limited_error_even_when_fresh():
    """A transient error should re-fetch immediately, ignoring max_age."""
    from build_float_cache import _is_stale
    fresh_ts = datetime.now(timezone.utc).isoformat()
    rec = {"float_shares": None,
           "fetched_at": fresh_ts,
           "error": "finviz_429_rate_limited"}
    assert _is_stale(rec, max_age_days=7) is True


def test_is_stale_returns_false_for_permanent_miss_when_fresh():
    """A clean finviz_404 (symbol genuinely doesn't exist) should NOT
    re-fetch every run — that wastes the rate budget."""
    from build_float_cache import _is_stale
    fresh_ts = datetime.now(timezone.utc).isoformat()
    rec = {"float_shares": None, "fetched_at": fresh_ts,
           "error": "finviz_404"}
    assert _is_stale(rec, max_age_days=7) is False


# ─── 6. End-to-end cache build (mocked HTTP) ─────────────────────────────

def test_build_cache_skips_fresh_entries(tmp_path, monkeypatch):
    """Headline scenario: 3 tickers, 1 already fresh → only 2 lookups."""
    import build_float_cache
    cache_path = tmp_path / "float_cache.json"
    # Pre-populate cache with one fresh entry
    fresh_ts = datetime.now(timezone.utc).isoformat()
    existing = {
        "AAPL": {"float_shares": 15_000_000_000, "source": "finviz",
                  "fetched_at": fresh_ts, "error": None},
    }
    cache_path.write_text(json.dumps(existing), encoding="utf-8")
    n_lookups = [0]

    def _fake_lookup(ticker, *, session=None, use_yfinance_fallback=True):
        n_lookups[0] += 1
        return {"float_shares": 5_000_000, "source": "finviz",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "error": None}

    monkeypatch.setattr(build_float_cache, "lookup_one", _fake_lookup)
    build_float_cache.build_cache(
        ["AAPL", "TSLA", "GME"],
        cache_path=cache_path, max_age_days=7,
        delay_sec=0.0, progress_every=10,
    )
    # AAPL was fresh → skipped. TSLA + GME → looked up.
    assert n_lookups[0] == 2
    final = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(final.keys()) == {"AAPL", "TSLA", "GME"}
    # AAPL value preserved
    assert final["AAPL"]["float_shares"] == 15_000_000_000


def test_build_cache_persists_after_each_progress_interval(tmp_path,
                                                              monkeypatch):
    """Robustness: even if the script crashes mid-run, partial cache
    is persisted (no lost work)."""
    import build_float_cache
    cache_path = tmp_path / "float_cache.json"
    saves = [0]
    orig_save = build_float_cache.save_cache

    def _counting_save(cache, path):
        saves[0] += 1
        orig_save(cache, path)

    monkeypatch.setattr(build_float_cache, "save_cache", _counting_save)
    monkeypatch.setattr(
        build_float_cache, "lookup_one",
        lambda t, **kw: {"float_shares": 1_000_000, "source": "finviz",
                          "fetched_at": datetime.now(timezone.utc).isoformat(),
                          "error": None},
    )
    build_float_cache.build_cache(
        [f"T{i}" for i in range(25)],
        cache_path=cache_path,
        max_age_days=7, delay_sec=0.0, progress_every=10,
    )
    # 25 tickers, save every 10 + final save → at least 3 saves
    assert saves[0] >= 3


# ─── 7. Cameron-strict smallcap filter ───────────────────────────────────

def test_cache_summary_counts_smallcaps_below_10m(tmp_path):
    """Sanity: the script reports how many cached entries qualify as
    Cameron-strict smallcaps (<10M float)."""
    import build_float_cache
    cache_path = tmp_path / "float_cache.json"
    cache = {
        "SMALL1": {"float_shares": 5_000_000},   # smallcap ✓
        "SMALL2": {"float_shares": 9_999_999},   # smallcap ✓ (just under)
        "EDGE":   {"float_shares": 10_000_000},  # NOT smallcap (cap exact)
        "LARGE":  {"float_shares": 50_000_000},  # NOT smallcap
        "NONE":   {"float_shares": None},         # uncountable
    }
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    loaded = build_float_cache.load_cache(cache_path)
    smallcap_count = sum(
        1 for r in loaded.values()
        if (r.get("float_shares") or 0) > 0
        and r["float_shares"] < 10_000_000
    )
    assert smallcap_count == 2  # SMALL1 + SMALL2 only
