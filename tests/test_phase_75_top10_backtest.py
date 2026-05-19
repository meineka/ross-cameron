"""Phase-75: Backtest universe-mismatch fix — TOP-10 per day.

User: "der scan soll genau so sein heute scanne ich alle aktien und
nehme 10 beste und trade da"

Audit found that backtest_bull_flag_v2.py iterated over ALL 1449
tickers in intraday_5m.parquet, while the live bot only watches the
TOP-10 per day from TradingView's premarket scan. This bias inflated
PnL numbers because backtest could "discover" winners on 1449-10 ≈
1439 symbols the live bot would never have watchlisted.

New script backtest_top10_per_day.py:
  - Reads candidates.parquet (gap≥10%, $2-20, RVOL≥2x already applied)
  - Sorts by score = intraday_pct × rvol_proxy DESC (same as live bot)
  - Takes TOP-10 per day
  - Runs bull-flag detector ONLY on those (ticker, day) pairs

These tests pin the ranking algorithm + universe selection so it
stays consistent with the live bot's `_premarket_scan_inner`.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_backtest"))


def _make_candidates():
    """Synthetic candidates.parquet — 3 days, 5 candidates each."""
    rows = []
    for date in ["2026-01-01", "2026-01-02", "2026-01-03"]:
        for i, (t, gap, rv) in enumerate([
            ("AAA", 50.0, 5.0),   # score = 250 (rank 1)
            ("BBB", 30.0, 8.0),   # score = 240 (rank 2)
            ("CCC", 100.0, 2.0),  # score = 200 (rank 3)
            ("DDD", 15.0, 10.0),  # score = 150 (rank 4)
            ("EEE", 12.0, 3.0),   # score =  36 (rank 5)
        ]):
            rows.append({"ticker": t, "date": pd.Timestamp(date),
                          "intraday_pct": gap, "rvol_proxy": rv,
                          "close": 5.0, "open": 5.0, "high": 5.5,
                          "low": 4.9, "volume": 1000000})
    return pd.DataFrame(rows)


# ─── 1. Ranking matches live bot ─────────────────────────────────────────

def test_pick_top10_uses_intraday_pct_x_rvol_score():
    """The live bot computes score = premarket_change × rvol in
    _premarket_scan_inner (bot.py around line 730). The backtest must
    use the SAME formula so the top-N is identical."""
    from backtest_top10_per_day import pick_top10_per_day
    cands = _make_candidates()
    top = pick_top10_per_day(cands, top_n=5)
    # For date=2026-01-01, ranking should be AAA(250) > BBB(240) > CCC(200) > DDD(150) > EEE(36)
    day1 = top[top["date"] == pd.Timestamp("2026-01-01")].sort_values("rank")
    assert list(day1["ticker"]) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert list(day1["rank"]) == [1, 2, 3, 4, 5]


def test_pick_top10_limits_to_top_n():
    """If 5 candidates exist but top_n=3, only top 3 by score."""
    from backtest_top10_per_day import pick_top10_per_day
    cands = _make_candidates()
    top = pick_top10_per_day(cands, top_n=3)
    # Each day should have exactly 3 entries
    counts = top.groupby("date").size()
    assert all(counts == 3), f"Per-day counts: {dict(counts)}"


def test_pick_top10_handles_fewer_than_n_days():
    """If a day has fewer candidates than top_n, take all of them."""
    from backtest_top10_per_day import pick_top10_per_day
    cands = _make_candidates()
    top = pick_top10_per_day(cands, top_n=10)
    # Each day has 5 candidates only; should get 5 back, not 10
    counts = top.groupby("date").size()
    assert all(counts == 5)


def test_pick_top10_score_descending_within_day():
    """The 'score' column must be sorted DESC within each day."""
    from backtest_top10_per_day import pick_top10_per_day
    cands = _make_candidates()
    top = pick_top10_per_day(cands, top_n=10)
    for _, day_grp in top.groupby("date"):
        scores = day_grp.sort_values("rank")["score"].tolist()
        assert scores == sorted(scores, reverse=True), (
            f"score not descending: {scores}"
        )


# ─── 2. Universe equivalence with live ──────────────────────────────────

def test_top10_universe_matches_live_bot_TOP_N():
    """Live bot's TOP_N = 10 (bot.py line 144). Backtest default
    must match."""
    import bot
    assert bot.TOP_N == 10


def test_pick_top10_default_matches_live_top_n():
    """The default top_n in pick_top10_per_day must be 10 to mirror
    live bot's TOP_N."""
    import inspect
    from backtest_top10_per_day import pick_top10_per_day
    sig = inspect.signature(pick_top10_per_day)
    default = sig.parameters["top_n"].default
    assert default == 10, (
        f"backtest default top_n={default}, but live bot TOP_N=10 — "
        f"they must match for the backtest to mean anything"
    )


# ─── 3. Source-grep — script structure ───────────────────────────────────

def test_backtest_script_exists():
    p = ROOT / "04_backtest" / "backtest_top10_per_day.py"
    assert p.exists()


def test_backtest_uses_intraday_5m_for_bars():
    src = (ROOT / "04_backtest" / "backtest_top10_per_day.py").read_text(
        encoding="utf-8"
    )
    assert "intraday_5m.parquet" in src


def test_backtest_reads_candidates_not_intraday_unique():
    """The OLD bug was `tickers = intraday['ticker'].unique()`. New
    script must instead read candidates.parquet and pick top-N.
    Phase-75.1 check: filter out docstring/comment mentions; the bug
    pattern must not appear as ACTUAL CODE."""
    src = (ROOT / "04_backtest" / "backtest_top10_per_day.py").read_text(
        encoding="utf-8"
    )
    # Strip docstrings + comments before checking
    lines_no_comments = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Toggle docstring state
            in_docstring = not in_docstring
            if stripped.count('"""') == 2 or stripped.count("'''") == 2:
                in_docstring = False  # single-line docstring
            continue
        if in_docstring:
            continue
        # Drop end-of-line comments but keep code
        if "#" in line:
            line = line.split("#", 1)[0]
        lines_no_comments.append(line)
    code = "\n".join(lines_no_comments)
    # NOT the buggy pattern in CODE (not comments)
    assert 'intraday["ticker"].unique()' not in code, (
        "Code contains the buggy unique-tickers pattern"
    )
    # IS the corrected pattern
    assert "candidates.parquet" in src
    assert "pick_top10_per_day" in src


def test_backtest_imports_detect_bull_flag_from_v2():
    """Reuses the existing pattern detector from v2 — single source
    of truth for the bull-flag logic. Phase-75.1: import is LAZY
    (inside main()) because v2 wraps stdout at module-top, which
    breaks pytest I/O capture if imported eagerly."""
    src = (ROOT / "04_backtest" / "backtest_top10_per_day.py").read_text(
        encoding="utf-8"
    )
    assert "backtest_bull_flag_v2" in src
    assert "detect_bull_flag" in src
    # Lazy-import pattern present
    assert "_lazy_v2" in src or "_v2 = None" in src


def test_backtest_supports_three_pattern_modes():
    """strict / moderate / loose — matches the live bot's three
    STRATEGY_VARIANT values."""
    src = (ROOT / "04_backtest" / "backtest_top10_per_day.py").read_text(
        encoding="utf-8"
    )
    assert "strict" in src and "moderate" in src and "loose" in src


# ─── 4. Output file naming ──────────────────────────────────────────────

def test_backtest_default_output_distinguishes_from_v2():
    """trades_top10_per_day.parquet must NOT clobber trades_v2.parquet
    (the old buggy one was kept for comparison)."""
    src = (ROOT / "04_backtest" / "backtest_top10_per_day.py").read_text(
        encoding="utf-8"
    )
    assert "trades_top10_per_day.parquet" in src
