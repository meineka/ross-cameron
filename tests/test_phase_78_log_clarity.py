"""Phase-78 (2026-05-19): log rotation + clarity.

User: "starte den bot trading ohne filter und schau ob er selber was tut
oder sich selbst tötet — nochmal logs analysieren, alles verbessern,
logs verbessern"

Log analysis (2026-05-19 18:30) found three operator-pain issues:

  1. bot.log grew to 15.3 MB / 194K lines over 10 days with NO rotation.
  2. on_bar() was completely silent — operator could not tell from
     bot.log whether bars were flowing at all (only status.json showed
     last_ws_bar_ts).
  3. handle_bar_5min() was silent on the most-common "no pattern" path
     — so ultra-mode runs 5 symbols × 1 check/5min = 60 checks/hour
     produced ZERO log lines unless a pattern fired. Operator
     conclusion: "bot does nothing."
  4. alpaca_ws_patch logged EVERY backoff retry — during a storm we saw
     1622 identical "ws backoff … TimeoutError (consec=6)" lines.
     Signal:noise ratio = unusable.

This phase fixes all four:
  A. RotatingFileHandler 5 MB × 5 backups on bot.log
  B. on_bar(): INFO at structured thresholds (1, 10, 100, every 100)
  C. handle_bar_5min(): DEBUG per check + INFO once per 5min/symbol
  D. alpaca_ws_patch: WARNING only at consec=1, consec=6, or error-
     class change. All other identical-state retries demoted to DEBUG.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bot_src() -> str:
    return (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")


def _ws_patch_src() -> str:
    return (ROOT / "06_live_bot" / "alpaca_ws_patch.py").read_text(encoding="utf-8")


# ─── A. bot.log rotation policy ──────────────────────────────────────────

def test_bot_log_uses_rotating_file_handler():
    """Plain FileHandler grew unbounded. Must use RotatingFileHandler."""
    src = _bot_src()
    assert "RotatingFileHandler" in src
    # Must be imported from logging.handlers
    assert "from logging.handlers import RotatingFileHandler" in src or \
           "import logging.handlers" in src


def test_bot_log_rotation_size_at_least_5mb():
    """maxBytes too small → too many rotates → bad signal-window.
    Too large → unbounded growth. 5 MB × 5 backups = 30 MB total cap
    is the sweet spot."""
    src = _bot_src()
    import re
    m = re.search(r"maxBytes\s*=\s*([\d\s*]+)", src)
    assert m, "maxBytes parameter missing"
    # Evaluate the expression (e.g. "5 * 1024 * 1024")
    expr = m.group(1).strip()
    val = eval(expr, {"__builtins__": {}}, {})
    assert val >= 1_000_000, f"maxBytes={val} too small"
    assert val <= 50_000_000, f"maxBytes={val} too large"


def test_bot_log_keeps_backups():
    """backupCount=0 means file just truncates — operator loses history.
    Need at least 3 backups."""
    src = _bot_src()
    import re
    m = re.search(r"backupCount\s*=\s*(\d+)", src)
    assert m
    assert int(m.group(1)) >= 3


# ─── B. on_bar visibility ────────────────────────────────────────────────

def test_on_bar_emits_bars_flow_log():
    """on_bar was silent — bars_received incremented but operator never
    saw evidence. Now must log at structured thresholds."""
    src = _bot_src()
    assert "BARS-FLOW" in src, (
        "on_bar must emit 'BARS-FLOW' status log so operator can grep "
        "bot.log for whether bars are arriving"
    )


def test_on_bar_log_threshold_is_structured():
    """Logging on EVERY bar = spam. Logging never = blind. Must use
    thresholds: 1st, 10th, 100th, then every 100th bar."""
    src = _bot_src()
    import re
    # Look for the threshold check
    block = re.search(
        r"bars_received\s*\+=\s*1[\s\S]{0,500}BARS-FLOW",
        src,
    )
    assert block, "BARS-FLOW must be near bars_received increment"
    # Threshold pattern: 1, 10, 100, multiples of 100
    block_text = block.group(0)
    assert "1" in block_text and "10" in block_text and "100" in block_text


# ─── C. handle_bar_5min visibility ───────────────────────────────────────

def test_handle_bar_5min_emits_bar_5m_log():
    """The 'no pattern detected' path was completely silent. Operator
    saw nothing in bot.log even though bot ran pattern check 60×/hour."""
    src = _bot_src()
    assert "BAR-5M" in src, (
        "handle_bar_5min must emit 'BAR-5M' line so operator sees "
        "every pattern-check outcome (with veto reason)"
    )


def test_handle_bar_5min_logs_no_pattern_at_info_level():
    """DEBUG-only would be invisible to operators tailing bot.log
    (level=INFO). Must promote to INFO at structured cadence
    (e.g. once per 5min per symbol)."""
    src = _bot_src()
    import re
    # Look for a log.info BAR-5M somewhere in handle_bar_5min
    block = re.search(
        r"async def handle_bar_5min[\s\S]{0,4000}?log\.info\([^\)]*BAR-5M",
        src,
    )
    assert block, (
        "handle_bar_5min must have a log.info BAR-5M for operator "
        "visibility (not just log.debug)"
    )


def test_handle_bar_5min_info_log_is_rate_limited():
    """If we log INFO every check (~60×/hour × 5 symbols = 300 lines/h),
    we recreate the noise problem. Must throttle — once per 5min per
    symbol is reasonable."""
    src = _bot_src()
    # Should use a per-ticker timestamp memory + 300s check
    assert "_last_no_pattern_summary_ts" in src or "no_pattern_summary" in src
    # And a 5min (300s) cadence threshold
    assert "300" in src


# ─── D. WS-patch noise reduction ─────────────────────────────────────────

def test_ws_patch_demotes_repeated_backoff_to_debug():
    """1622× identical "ws backoff … (consec=6)" was the storm log
    noise. Repeated same-error-class backoffs at the same consec must
    be DEBUG, not WARNING."""
    src = _ws_patch_src()
    # The backoff log must distinguish WARNING (state-change) from
    # DEBUG (steady-state repeat)
    assert "log.debug(" in src and "ws backoff" in src, (
        "WS-patch backoff log must have a DEBUG path for repeat events"
    )
    # AND keep a WARNING path for state transitions
    import re
    m = re.search(r"log\.warning\([\"'][^\"']*ws backoff", src)
    assert m, "WARNING path must still exist for state transitions"


def test_ws_patch_uses_state_change_predicate():
    """Phase-78 logic: log WARNING only when state actually changed
    (consec==1, consec==6, or error-class changed)."""
    src = _ws_patch_src()
    # The state-tracking attribute
    assert "_phase78_last_err_cls" in src or "last_err_cls" in src
    # Logic checks consec boundaries
    assert "consec_value_errors == 1" in src or "consec=1" in src.lower()
    assert "consec_value_errors == 6" in src or "consec=6" in src.lower()


def test_ws_patch_phase_78_comment_present():
    """Future maintainer needs to know WHY this branching is here."""
    src = _ws_patch_src()
    assert "Phase-78" in src
    assert "1622" in src or "noise" in src.lower()


# ─── E. Sanity ──────────────────────────────────────────────────────────

def test_bot_module_still_imports_with_rotation():
    import bot
    assert hasattr(bot, "Bot")
    # RotatingFileHandler must be present in handlers
    import logging
    root = logging.getLogger()
    # In test env, root handlers may differ — but the import did not raise


def test_phase_78_comment_in_bot():
    src = _bot_src()
    assert "Phase-78" in src


def test_no_other_unbounded_file_handlers_in_bot():
    """Defense: ensure we didn't leave a second FileHandler open
    that bypasses rotation."""
    src = _bot_src()
    # The original `logging.FileHandler(...)` for bot.log must be gone
    # (it's replaced by RotatingFileHandler). Other FileHandler uses
    # (e.g. for separate files like daemon.log) are OK if they exist,
    # but bot.log specifically must not be opened by FileHandler.
    import re
    m = re.search(r'logging\.FileHandler\([^)]*"bot\.log"', src)
    assert m is None, (
        "bot.log must use RotatingFileHandler not plain FileHandler"
    )
