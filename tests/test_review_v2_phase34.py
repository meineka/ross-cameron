"""Review-V2 Phase 3+4 behavior tests:
  P0.4 — safe_bracket _pre_entry_quote_safety wired
  P1.5 — SPY intraday refresh in trading-loop
  P1.6 — RESCAN_FAST_PHASE_END enforced
  P1.2 — two_source_scan alpaca-fallback wired
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bot_src() -> str:
    return (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")


# ─── P0.4: safe_bracket wired into entry ─────────────────────────────────────
def test_pre_entry_quote_safety_called_before_bracket_buy():
    """The new pre_entry_quote_safety helper must be invoked before the
    submit_bracket_buy call in handle_bar_5min."""
    src = _bot_src()
    # Find handle_bar_5min entry submission block
    idx = src.find("SUBMITTING BRACKET-BUY")
    assert idx > 0
    # 1000 chars BEFORE the submit log should contain the quote-safety call
    before = src[max(0, idx - 1500):idx]
    assert "_pre_entry_quote_safety" in before, \
        "quote-safety check not wired before submit_bracket_buy"


def test_pre_entry_quote_safety_uses_safe_bracket_check_liquidity():
    """The helper must import and delegate to safe_bracket.check_liquidity
    (no parallel re-implementation that diverges from the audited module)."""
    src = _bot_src()
    idx = src.find("def _pre_entry_quote_safety")
    assert idx > 0
    body = src[idx: idx + 2000]
    assert "from safe_bracket import check_liquidity" in body
    assert "check_liquidity(snap)" in body


def test_day_state_has_quote_safety_counter():
    import bot
    d = bot.DayState()
    assert hasattr(d, "patterns_rejected_quote_safety")
    assert d.patterns_rejected_quote_safety == 0


# ─── P1.5: SPY intraday refresh in trading loop ──────────────────────────────
def test_spy_intraday_refresh_in_slow_rescan():
    """The slow-rescan block must include SPY refresh logic."""
    src = _bot_src()
    # The slow-rescan branch should now include fetch_spy_today_pct
    idx = src.find("SLOW Re-Scan")
    assert idx > 0
    block = src[idx: idx + 1500]
    assert "fetch_spy_today_pct" in block, "SPY refresh missing from slow-rescan"
    assert "compute_spy_size_multiplier" in block


# ─── P1.6: RESCAN_FAST_PHASE_END enforcement ─────────────────────────────────
def test_fast_rescan_guarded_by_phase_end():
    """Fast-rescan must check ny.time() < RESCAN_FAST_PHASE_END."""
    src = _bot_src()
    idx = src.find("FAST Re-Scan")
    assert idx > 0
    block = src[idx: idx + 800]
    assert "RESCAN_FAST_PHASE_END" in block, \
        "fast-rescan not guarded by RESCAN_FAST_PHASE_END"


# ─── P1.2: two_source alpaca-fallback wired ──────────────────────────────────
def test_two_source_alpaca_fallback_wired():
    """When yfinance is degraded, code must actually call
    alpaca_universe_snapshot (not just log a TODO)."""
    src = _bot_src()
    # Old code had: log.warning("...Alpaca-fallback nicht aktiv...TODO wire...")
    # Should no longer be there.
    assert "TODO wire alpaca_universe_snapshot" not in src, \
        "still has TODO — alpaca-fallback not wired"
    assert "alpaca_universe_snapshot(data_client" in src, \
        "alpaca_universe_snapshot not called"


def test_delisted_marking_deferred_until_two_source_check():
    """Old code marked delisted on first yfinance miss. New code must
    defer until two-source verification."""
    src = _bot_src()
    assert "yfinance_missing_symbols" in src, \
        "deferred-marking buffer not present"
    assert "truly_delisted" in src, \
        "two-source truly_delisted set not present"
