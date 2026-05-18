"""Phase-72: STRATEGY_VARIANT=ultra — looser than loose + skip entry vetos.

User: "hat er wieder nicht getradet / improve ich hätte gerne dass er
sehr locker heute noch trades machen kann"

After Phase-69 loose mode still produced zero trades (status.json
showed "no pattern detected" on every watchlist symbol), the user
asked for an even more permissive variant. Phase-72 adds "ultra":
  - Same 2x sizing as loose/relaxed
  - Even looser entry thresholds (pole 1%, retrace 90%, breakout 1.0x)
  - DISABLE_ENTRY_VETOS=True: skip VWAP/MACD/FBO checks entirely

NOT for live money. Pure paper-mode execution validation.
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


def _reimport_bot(variant: str | None, monkeypatch) -> object:
    if variant is None:
        monkeypatch.delenv("STRATEGY_VARIANT", raising=False)
    else:
        monkeypatch.setenv("STRATEGY_VARIANT", variant)
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE",
                          Path("/tmp/phase72_nonexistent.env"))
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    return sys.modules["bot"]


@pytest.fixture(autouse=True)
def _restore_strict_after():
    yield
    os.environ["STRATEGY_VARIANT"] = "strict"
    if "bot" in sys.modules:
        del sys.modules["bot"]
    importlib.import_module("bot")


# ─── 1. Ultra constants ───────────────────────────────────────────────────

def test_ultra_uses_2x_sizing_same_as_loose(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.STRATEGY_VARIANT == "ultra"
    assert bot.MAX_LOSS_PER_TRADE_USD == 100.0
    assert bot.DAILY_MAX_LOSS_USD == 300.0
    assert bot.EQUITY_RISK_CAP_PCT == 2.0


def test_ultra_loosens_pole_to_1pct(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.POLE_MIN_MOVE_PCT == 1.0  # was 2.5 loose / 4.0 strict
    assert bot.POLE_MIN_CANDLES == 1     # was 2 loose / 3 strict
    assert bot.POLE_MAX_CANDLES == 15    # was 10 loose / 7 strict
    assert bot.POLE_TOPPING_TAIL_MAX == 0.9  # was 0.7 loose / 0.5 strict


def test_ultra_loosens_flag_to_8_candles(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.FLAG_MAX_CANDLES == 8     # was 4 loose / 3 strict
    assert bot.FLAG_RETRACE_MAX_PCT == 90.0  # was 70 loose / 50 strict


def test_ultra_breakout_volume_1x(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.BREAKOUT_VOL_FACTOR == 1.0  # any breakout volume OK


def test_ultra_daily_gain_3pct(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT == 3.0  # was 5 loose / 10 strict


def test_ultra_rvol_2x(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.RVOL_MIN_PROXY == 2.0  # was 3 loose / 5 strict


def test_ultra_catalyst_off(monkeypatch):
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.CATALYST_MODE == "off"


def test_ultra_disables_entry_vetos(monkeypatch):
    """The KEY difference vs loose: VWAP/MACD/FBO checks are skipped."""
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.DISABLE_ENTRY_VETOS is True


# ─── 2. Other variants don't get ultra leak ───────────────────────────────

def test_strict_keeps_vetos_active(monkeypatch):
    bot = _reimport_bot("strict", monkeypatch)
    assert bot.DISABLE_ENTRY_VETOS is False
    assert bot.POLE_MIN_MOVE_PCT == 4.0


def test_loose_keeps_vetos_active(monkeypatch):
    """loose loosens thresholds but KEEPS VWAP/MACD/FBO entry vetos."""
    bot = _reimport_bot("loose", monkeypatch)
    assert bot.DISABLE_ENTRY_VETOS is False
    assert bot.POLE_MIN_MOVE_PCT == 2.5  # loose value, NOT ultra's 1.0


def test_relaxed_keeps_strict_pattern(monkeypatch):
    bot = _reimport_bot("relaxed", monkeypatch)
    assert bot.DISABLE_ENTRY_VETOS is False
    assert bot.POLE_MIN_MOVE_PCT == 4.0


# ─── 3. Invalid value defaults to strict (safety) ─────────────────────────

def test_invalid_variant_falls_back_to_strict(monkeypatch):
    """Defensive: unknown variant MUST NOT enable ultra (or any
    permissive mode). Falls back to strict."""
    bot = _reimport_bot("yolo", monkeypatch)
    assert bot.STRATEGY_VARIANT == "strict"
    assert bot.DISABLE_ENTRY_VETOS is False


# ─── 4. Price/Float bounds unchanged ─────────────────────────────────────

def test_price_float_bounds_same_for_ultra(monkeypatch):
    """Cameron's small-cap universe is invariant — even ultra trades
    only $2-$20 stocks with <10M float."""
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.PRICE_MIN == 2.0
    assert bot.PRICE_MAX == 20.0
    assert bot.FLOAT_MAX_SHARES == 10_000_000


# ─── 5. detect_bull_flag respects DISABLE_ENTRY_VETOS ────────────────────

def test_ultra_detect_bull_flag_skips_vwap_check_in_source():
    """Source-grep: the veto-disable wrapping must be present in the
    detect_bull_flag function so a future refactor can't accidentally
    un-skip the checks."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "if not DISABLE_ENTRY_VETOS:" in src
    assert 'return False, {"_veto": "vwap"}' in src
    # Source-grep: DISABLE_ENTRY_VETOS must be defined at module level
    assert "DISABLE_ENTRY_VETOS = False" in src
    # And set to True in ultra branch
    assert "DISABLE_ENTRY_VETOS = True" in src


# ─── 6. Startup-log markers ──────────────────────────────────────────────

def test_startup_log_announces_ultra_mode():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "ultra-algo" in src
    assert "ULTRA-loose entries + VWAP/MACD/FBO disabled" in src
    assert "DISABLE_ENTRY_VETOS = True" in src


# ─── 7. Sanity: ultra still rejects garbage ─────────────────────────────

def test_ultra_still_has_minimum_filters(monkeypatch):
    """Even ultra has POSITIVE thresholds — not zero."""
    bot = _reimport_bot("ultra", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT > 0
    assert bot.RVOL_MIN_PROXY > 1.0
    assert bot.POLE_MIN_MOVE_PCT > 0
    assert bot.FLAG_RETRACE_MAX_PCT < 100  # not "any retrace"
