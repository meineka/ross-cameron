"""Phase-79 (2026-05-19): FORCE_TRADE_MODE — paper-only end-to-end stress.

User: "mach mal alle constraints weg dass er free traden kann und lass
ihn alle 5 minuten was prüfen"

Translation: remove ALL entry constraints so the bot trades freely and
runs an entry check every 5 minutes.

This adds STRATEGY_VARIANT=force which bypasses the bull-flag pattern
detector entirely. Every 5-min bar (per symbol) where the bot is not
already in a position triggers a synthetic BUY signal at the current
close with a 1% stop / 2% target. Position sizing envelope is tiny
($20 max loss / 0.5% equity cap) to keep the $100k paper account safe
even if every single trade loses.

NOT a strategy — purely a test of the live-trading code path.
"""
from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bot_src() -> str:
    return (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")


# ─── A. The force variant is recognized ─────────────────────────────────

def test_force_variant_in_threshold_block():
    """force-algo must appear in the same threshold-config block as
    strict/relaxed/loose/ultra so the operator can pick it via env."""
    src = _bot_src()
    assert 'STRATEGY_VARIANT == "force"' in src


def test_force_variant_sets_force_entry_flag():
    """The whole point — force mode flips FORCE_ENTRY_ON_BAR to True."""
    src = _bot_src()
    import re
    block = re.search(
        r'STRATEGY_VARIANT == "force"[\s\S]{0,1500}FORCE_ENTRY_ON_BAR\s*=\s*True',
        src,
    )
    assert block, (
        "force variant block must set FORCE_ENTRY_ON_BAR = True"
    )


def test_force_entry_default_is_false():
    """The flag must default to False so other variants keep working."""
    src = _bot_src()
    # The default assignment before the variant branching
    import re
    m = re.search(r"^FORCE_ENTRY_ON_BAR\s*=\s*False",
                  src, flags=re.MULTILINE)
    assert m, "FORCE_ENTRY_ON_BAR must default to False"


# ─── B. Position sizing is small ────────────────────────────────────────

def test_force_max_loss_per_trade_small():
    """Paper-only stress mode — keep loss-per-trade small so dumb
    trades don't burn the $100k account."""
    src = _bot_src()
    import re
    block = re.search(
        r'elif STRATEGY_VARIANT == "force"[\s\S]{0,800}'
        r'MAX_LOSS_PER_TRADE_USD\s*=\s*([\d.]+)',
        src,
    )
    assert block, "force-algo MAX_LOSS_PER_TRADE_USD missing"
    val = float(block.group(1))
    assert val <= 50.0, f"force-mode MAX_LOSS too large: ${val}"


def test_force_equity_cap_small():
    src = _bot_src()
    import re
    block = re.search(
        r'elif STRATEGY_VARIANT == "force"[\s\S]{0,800}'
        r'EQUITY_RISK_CAP_PCT\s*=\s*([\d.]+)',
        src,
    )
    assert block
    val = float(block.group(1))
    assert val <= 1.0, f"force-mode equity-cap too high: {val}%"


# ─── C. handle_bar_5min injects synthetic signal ────────────────────────

def test_handle_bar_5min_force_path_synthesizes_signal():
    """When FORCE_ENTRY_ON_BAR is True and we have enough bars + no
    position, the code must skip the detector and BUY at close."""
    src = _bot_src()
    import re
    # Look for the FORCE-ENTRY branch
    block = re.search(
        r"if FORCE_ENTRY_ON_BAR[\s\S]{0,800}signal\s*=\s*True",
        src,
    )
    assert block, (
        "handle_bar_5min must short-circuit to signal=True when "
        "FORCE_ENTRY_ON_BAR is set"
    )


def test_force_entry_uses_close_as_entry_price():
    """Force-entry takes current bar close as entry. Stop = close*0.99
    (1% stop), target = close*1.02 (2% target = 2R)."""
    src = _bot_src()
    # Synthetic params block must reference close-based pricing
    assert "0.99" in src or "* 0.99" in src
    assert "1.02" in src or "* 1.02" in src


def test_force_entry_only_when_not_in_position():
    """Must not re-fire entry if symbol already has open position."""
    src = _bot_src()
    import re
    block = re.search(
        r"if FORCE_ENTRY_ON_BAR.*?not ts\.in_position",
        src,
    )
    assert block, (
        "FORCE_ENTRY must check `not ts.in_position` to avoid re-entry"
    )


def test_force_entry_logs_at_info_level():
    """Operator must see "FORCE-ENTRY <sym>" in bot.log for every
    synthetic entry."""
    src = _bot_src()
    assert "FORCE-ENTRY" in src
    import re
    # The log.info line for force entry
    assert re.search(r"log\.info\([^)]*FORCE-ENTRY", src)


# ─── D. Bypass 3rd-pullback skip ────────────────────────────────────────

def test_force_mode_bypasses_third_pullback_skip():
    """The 3rd-pullback rule normally caps entries per symbol per day.
    Force-mode wants unlimited so it must skip this check."""
    src = _bot_src()
    import re
    # The pullback check must mention FORCE_ENTRY_ON_BAR
    block = re.search(
        r"pullback_count_today\s*>=\s*3[\s\S]{0,200}FORCE_ENTRY_ON_BAR",
        src,
    )
    assert block, (
        "3rd-pullback skip must be gated by `not FORCE_ENTRY_ON_BAR`"
    )


# ─── E. Phase-79 archaeology + import ───────────────────────────────────

def test_phase_79_explanation_comment():
    src = _bot_src()
    assert "Phase-79" in src
    assert "force" in src.lower()


def test_bot_imports_with_force_variant(monkeypatch):
    """STRATEGY_VARIANT=force at module import time must not crash."""
    monkeypatch.setenv("STRATEGY_VARIANT", "force")
    # Force fresh import
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "force"
    assert bot.FORCE_ENTRY_ON_BAR is True
    # Position-sizing envelope is small
    assert bot.MAX_LOSS_PER_TRADE_USD <= 50.0


def test_bot_imports_with_ultra_variant_unchanged(monkeypatch):
    """Verify Phase-79 didn't break ultra mode — FORCE_ENTRY_ON_BAR
    must still be False there."""
    monkeypatch.setenv("STRATEGY_VARIANT", "ultra")
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "ultra"
    assert bot.FORCE_ENTRY_ON_BAR is False
    assert bot.DISABLE_ENTRY_VETOS is True  # ultra still skips vetos


def test_strict_variant_force_flag_false(monkeypatch):
    """Strict mode (default) must NOT enable force entry."""
    monkeypatch.setenv("STRATEGY_VARIANT", "strict")
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.FORCE_ENTRY_ON_BAR is False
