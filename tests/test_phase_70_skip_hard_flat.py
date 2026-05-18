"""Phase-70: SKIP_HARD_FLAT_TODAY env-var override.

User-request 2026-05-18: bot was respawned in loose-mode at 18:52
Berlin = 12:52 NY — AFTER the 12:00 NY HARD_FLAT, so it went straight
to sleep without trading. User wants today's afternoon (12:52-16:00
NY) used for trading instead of waiting until tomorrow.

SKIP_HARD_FLAT_TODAY=1 pushes TIME_HARD_FLAT from 12:00 NY to 15:55
NY (5 min before close) and TIME_NEW_ENTRIES_END from 11:30 to 15:30.

Cameron's strict rule of "stop at noon NY" is preserved as default
because afternoon trading has lower historical edge. This override is
ONLY for sessions where the operator wants to validate end-to-end
execution on quiet days.
"""
from __future__ import annotations
import importlib
import os
import sys
from datetime import time as dtime
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _reimport_bot(skip_hard_flat: str | None, monkeypatch) -> object:
    if skip_hard_flat is None:
        monkeypatch.delenv("SKIP_HARD_FLAT_TODAY", raising=False)
    else:
        monkeypatch.setenv("SKIP_HARD_FLAT_TODAY", skip_hard_flat)
    monkeypatch.setenv("STRATEGY_VARIANT", "strict")  # isolate from variant tests
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE",
                          Path("/tmp/phase70_nonexistent.env"))
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    return sys.modules["bot"]


@pytest.fixture(autouse=True)
def _restore_defaults_after():
    """Reset env to test-suite defaults after each test. Use os.environ
    direct (NOT pop) so the value is explicit "0" not missing — that way
    sibling tests in other modules get the same baseline."""
    yield
    os.environ["SKIP_HARD_FLAT_TODAY"] = "0"
    os.environ["STRATEGY_VARIANT"] = "strict"
    if "bot" in sys.modules:
        del sys.modules["bot"]
    importlib.import_module("bot")


# ─── 1. Default: Cameron-strict 12:00 NY ────────────────────────────────

def test_default_hard_flat_is_noon_ny(monkeypatch):
    bot = _reimport_bot(None, monkeypatch)
    assert bot.TIME_HARD_FLAT == dtime(12, 0)
    assert bot.TIME_NEW_ENTRIES_END == dtime(11, 30)
    assert bot.SKIP_HARD_FLAT_TODAY is False


def test_explicit_zero_keeps_strict(monkeypatch):
    bot = _reimport_bot("0", monkeypatch)
    assert bot.TIME_HARD_FLAT == dtime(12, 0)
    assert bot.SKIP_HARD_FLAT_TODAY is False


# ─── 2. Override active: pushes to 15:55 NY ─────────────────────────────

def test_skip_hard_flat_pushes_to_1555_ny(monkeypatch):
    bot = _reimport_bot("1", monkeypatch)
    assert bot.SKIP_HARD_FLAT_TODAY is True
    assert bot.TIME_HARD_FLAT == dtime(15, 55)
    assert bot.TIME_NEW_ENTRIES_END == dtime(15, 30)


def test_skip_hard_flat_pre_close_buffer_5min(monkeypatch):
    """Spec: HARD_FLAT must always be at least 5 min before market
    close (16:00 NY) so SL/TP have time to fill."""
    bot = _reimport_bot("1", monkeypatch)
    market_close = dtime(16, 0)
    # 5 min before close = 15:55
    diff_minutes = (market_close.hour * 60 + market_close.minute
                     - bot.TIME_HARD_FLAT.hour * 60
                     - bot.TIME_HARD_FLAT.minute)
    assert diff_minutes >= 5, (
        f"HARD_FLAT too close to market-close: {diff_minutes}min "
        f"(must be >=5min)"
    )


# ─── 3. Accept multiple truthy values ───────────────────────────────────

@pytest.mark.parametrize("truthy", ["1", "true", "True", "TRUE", "yes",
                                       "YES", "on", "ON"])
def test_truthy_values_enable_override(truthy, monkeypatch):
    bot = _reimport_bot(truthy, monkeypatch)
    assert bot.SKIP_HARD_FLAT_TODAY is True
    assert bot.TIME_HARD_FLAT == dtime(15, 55)


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "",
                                      "bogus", "maybe"])
def test_falsy_values_keep_strict(falsy, monkeypatch):
    bot = _reimport_bot(falsy, monkeypatch)
    assert bot.SKIP_HARD_FLAT_TODAY is False
    assert bot.TIME_HARD_FLAT == dtime(12, 0)


# ─── 4. Strategy-variant orthogonality ─────────────────────────────────

def test_override_works_with_strict_variant(monkeypatch):
    monkeypatch.setenv("STRATEGY_VARIANT", "strict")
    monkeypatch.setenv("SKIP_HARD_FLAT_TODAY", "1")
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "strict"
    assert bot.SKIP_HARD_FLAT_TODAY is True
    assert bot.TIME_HARD_FLAT == dtime(15, 55)


def test_override_works_with_loose_variant(monkeypatch):
    monkeypatch.setenv("STRATEGY_VARIANT", "loose")
    monkeypatch.setenv("SKIP_HARD_FLAT_TODAY", "1")
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE",
                          Path("/tmp/phase70_nonexistent.env"))
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "loose"
    assert bot.SKIP_HARD_FLAT_TODAY is True
    assert bot.TIME_HARD_FLAT == dtime(15, 55)
    # And the loose entry thresholds still apply
    assert bot.CATALYST_MODE == "off"


# ─── 5. Source-grep contract ────────────────────────────────────────────

def test_startup_log_announces_override():
    """Operator-visibility: if override is on, daemon.log must shout
    so postmortems unambiguously show afternoon-trading was active."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "SKIP_HARD_FLAT_TODAY=1" in src
    assert "afternoon trading enabled" in src


def test_other_time_cuts_unchanged_by_override(monkeypatch):
    """Override touches END + HARD_FLAT only — TIME_RTH_START and
    TIME_NEW_ENTRIES_START must NOT shift."""
    bot = _reimport_bot("1", monkeypatch)
    assert bot.TIME_RTH_START == dtime(9, 30)
    assert bot.TIME_NEW_ENTRIES_START == dtime(9, 35)
