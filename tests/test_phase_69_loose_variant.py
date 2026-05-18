"""Phase-69: STRATEGY_VARIANT=loose for emergency "no trade today" sessions.

User: "also ich wollte doch dass du ein bisschen den locker machst für
ein paar stunden, dass er tradet"

After Phase-68 fixed the WS connection-limit cascade, the bot could
finally subscribe again — but Cameron-strict entry filters (RVOL>=5x,
gain>=10%, pole-move>=4%, catalyst<=24h) rejected every candidate.
User wants a TEMPORARY loose mode that:
  - Same 2× position-size as relaxed (Phase-66)
  - Phase-33 "see-some-trades" entry thresholds (lower bars)
  - Catalyst filter OFF (CATALYST_MODE="off")

This is NOT for live trading with real money. It's a tuning knob for
sessions where the operator wants to force entries to validate the
full execution path end-to-end.
"""
from __future__ import annotations
import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _reimport_bot_with_variant(variant: str | None, monkeypatch) -> object:
    if variant is None:
        monkeypatch.delenv("STRATEGY_VARIANT", raising=False)
    else:
        monkeypatch.setenv("STRATEGY_VARIANT", variant)
    # Block .env from leaking into the reimport
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE",
                          Path("/tmp/nonexistent_phase69.env"))
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    return sys.modules["bot"]


@pytest.fixture(autouse=True)
def _restore_strict_after():
    """Phase-69 tests reimport bot.py with different variants. Without
    explicit teardown, the LAST-loaded variant leaks into subsequent
    suite-tests that just do `import bot` and expect strict defaults.

    Important: use os.environ direct, NOT monkeypatch.setenv — the
    latter would revert to whatever was there before the test (which
    might be 'loose' from a prior leaked state) when monkeypatch
    cleans up its scope."""
    import os
    yield
    os.environ["STRATEGY_VARIANT"] = "strict"
    if "bot" in sys.modules:
        del sys.modules["bot"]
    importlib.import_module("bot")


# ─── 1. Loose variant constants ──────────────────────────────────────────

def test_loose_uses_2x_sizing_like_relaxed(monkeypatch):
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.STRATEGY_VARIANT == "loose"
    assert bot.MAX_LOSS_PER_TRADE_USD == 100.0
    assert bot.DAILY_MAX_LOSS_USD == 300.0
    assert bot.EQUITY_RISK_CAP_PCT == 2.0


def test_loose_loosens_daily_gain_threshold(monkeypatch):
    """5% gain (was 10% strict) — catches mid-range premarket movers
    that strict would reject."""
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT == 5.0


def test_loose_loosens_rvol_threshold(monkeypatch):
    """3x RVOL (was 5x strict) — Phase-33 value."""
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.RVOL_MIN_PROXY == 3.0


def test_loose_loosens_pole_pattern(monkeypatch):
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.POLE_MIN_CANDLES == 2  # was 3
    assert bot.POLE_MAX_CANDLES == 10  # was 7
    assert bot.POLE_MIN_MOVE_PCT == 2.5  # was 4.0
    assert bot.POLE_TOPPING_TAIL_MAX == 0.7  # was 0.5


def test_loose_loosens_flag_pattern(monkeypatch):
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.FLAG_MIN_CANDLES == 1
    assert bot.FLAG_MAX_CANDLES == 4  # was 3
    assert bot.FLAG_RETRACE_MAX_PCT == 70.0  # was 50.0


def test_loose_loosens_breakout_volume(monkeypatch):
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.BREAKOUT_VOL_FACTOR == 1.2  # was 1.5


def test_loose_disables_catalyst_filter(monkeypatch):
    """The KEY change vs relaxed — most "no trade" days are because
    no candidate had a fresh 8-K. Loose-mode skips that filter entirely."""
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.CATALYST_MODE == "off"


# ─── 2. Other variants don't accidentally get loose treatment ──────────

def test_strict_keeps_strict_entries(monkeypatch):
    bot = _reimport_bot_with_variant("strict", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT == 10.0
    assert bot.RVOL_MIN_PROXY == 5.0
    assert bot.POLE_MIN_MOVE_PCT == 4.0
    assert bot.POLE_TOPPING_TAIL_MAX == 0.5
    assert bot.FLAG_RETRACE_MAX_PCT == 50.0
    assert bot.BREAKOUT_VOL_FACTOR == 1.5
    assert bot.CATALYST_MODE == "soft"


def test_relaxed_keeps_strict_entries(monkeypatch):
    """relaxed only doubles SIZE, not entry thresholds — that
    distinction is the whole point of having 3 variants."""
    bot = _reimport_bot_with_variant("relaxed", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT == 10.0
    assert bot.RVOL_MIN_PROXY == 5.0
    assert bot.POLE_MIN_MOVE_PCT == 4.0
    assert bot.POLE_TOPPING_TAIL_MAX == 0.5
    assert bot.CATALYST_MODE == "soft"


# ─── 3. Price/Float bounds unchanged ────────────────────────────────────

def test_price_float_bounds_same_for_all_variants(monkeypatch):
    """Cameron's price-range and float-cap define the SMALLCAP universe —
    no variant should shift these or it'd trade megacaps."""
    for variant in ("strict", "relaxed", "loose"):
        bot = _reimport_bot_with_variant(variant, monkeypatch)
        assert bot.PRICE_MIN == 2.0, f"{variant} broke PRICE_MIN"
        assert bot.PRICE_MAX == 20.0, f"{variant} broke PRICE_MAX"
        assert bot.FLOAT_MAX_SHARES == 10_000_000, f"{variant} broke float"


# ─── 4. Sanity: loose still has higher bars than "no filter at all" ───

def test_loose_still_filters_garbage(monkeypatch):
    """A truly-broken stock (0% gain, no volume) must STILL be rejected
    in loose mode. Loose loosens; it doesn't eliminate filters."""
    bot = _reimport_bot_with_variant("loose", monkeypatch)
    assert bot.DAILY_GAIN_MIN_PCT > 0
    assert bot.RVOL_MIN_PROXY > 1.0
    assert bot.POLE_MIN_MOVE_PCT > 0


# ─── 5. Startup-log markers ─────────────────────────────────────────────

def test_startup_log_mentions_loose_in_label_map():
    """daemon.log must print which variant is active. Operator should
    NEVER be confused about which thresholds are in play."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert '"loose"' in src
    assert "loose-algo" in src
    assert "POLE_MIN_MOVE_PCT" in src
    assert "CATALYST_MODE" in src
