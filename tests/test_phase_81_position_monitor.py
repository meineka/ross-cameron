"""Phase-81 (2026-05-19): position monitor task — logs entries + exits.

User: "hat tp eingegriffen, hat sl eingegriffen — wenn nötig log
erweitern"

Bracket child legs (TP-limit + SL-stop) fire SERVER-SIDE on Alpaca.
The bot never gets a Python-side notification, so bot.log was silent
on all exits. Operator could see BUY events but couldn't tell when/
why the position closed without querying Alpaca directly.

Phase-81 adds a `position_monitor` async task that polls
`self.executor.client.get_all_positions()` every 30 seconds, diffs
against the previous tick, and emits:
  - POS-OPEN  when a symbol appears
  - POS-CLOSE when a symbol disappears (heuristic SL/TP/even-flat)
  - POS-PNL   every 5 min (10 ticks) for every still-open position

Source-grep tests pin the contract.
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


# ─── A. position_monitor task exists and is launched ────────────────────

def test_position_monitor_task_defined():
    src = _bot_src()
    assert "async def position_monitor(" in src or \
           "def position_monitor(" in src
    assert "position_monitor started" in src


def test_position_monitor_polls_alpaca():
    src = _bot_src()
    # Must call _safe_get_positions (or client.get_all_positions) every tick
    assert "_safe_get_positions" in src
    assert "self.executor.client.get_all_positions" in src


def test_position_monitor_launched_as_task():
    """The monitor must be created with asyncio.create_task so it
    actually runs concurrently with ws_loop/time_loop."""
    src = _bot_src()
    assert "pos_task = asyncio.create_task(position_monitor()" in src


def test_position_monitor_polls_every_30s():
    """30s is a sweet spot: bracket fills are visible within 30s of
    the actual fill, but rate-limit cost is ~2 calls/min = OK."""
    src = _bot_src()
    import re
    # The await asyncio.sleep(30) inside the monitor loop
    block = re.search(
        r"position_monitor\(\)[\s\S]{0,3000}?await asyncio\.sleep\(\s*30\s*\)",
        src,
    )
    # Allow either flat-30 or a named constant — both must yield 30s
    assert block, "position_monitor must sleep ~30s between polls"


# ─── B. Emits OPEN / CLOSE / PNL lines ─────────────────────────────────

def test_position_monitor_logs_position_open():
    src = _bot_src()
    assert "POS-OPEN" in src


def test_position_monitor_logs_position_close():
    src = _bot_src()
    assert "POS-CLOSE" in src


def test_position_close_distinguishes_sl_vs_tp():
    """When a position disappears, the log should at least guess
    SL-HIT vs TP-HIT based on the last-known price vs entry."""
    src = _bot_src()
    assert "SL-HIT" in src
    assert "TP-HIT" in src


def test_position_monitor_periodic_pnl_snapshot():
    """Every ~5min the bot should log a POS-PNL line per open
    position so the operator can grep bot.log for ongoing trade
    state."""
    src = _bot_src()
    assert "POS-PNL" in src


# ─── C. Task cancellation on shutdown ───────────────────────────────────

def test_position_monitor_cancelled_on_shutdown():
    """When ws_loop or time_loop exits (HARD_FLAT or critical error),
    position_monitor must also be cancelled — otherwise it leaks an
    async task that keeps polling after the bot tries to shut down."""
    src = _bot_src()
    assert "pos_task.cancel()" in src
    # And awaited with timeout so we don't hang on shutdown
    assert "await asyncio.wait_for(pos_task" in src


def test_position_monitor_handles_cancellederror():
    """asyncio.CancelledError must NOT crash the monitor — it should
    return cleanly so the await asyncio.wait_for(...) doesn't hang."""
    src = _bot_src()
    import re
    block = re.search(
        r"position_monitor\(\)[\s\S]{0,4000}?asyncio\.CancelledError",
        src,
    )
    assert block, "position_monitor must catch CancelledError"


# ─── D. Sanity ──────────────────────────────────────────────────────────

def test_phase_81_comment_present():
    src = _bot_src()
    assert "Phase-81" in src


def test_bot_imports_cleanly():
    import bot
    assert hasattr(bot, "Bot")
