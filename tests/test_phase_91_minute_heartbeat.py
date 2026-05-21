"""Phase-91 (2026-05-21): every-minute heartbeat ntfy with watchlist.

User: "mach neu der bot soll alle 60 sekunden ein lebenszeichen schicken.
Bot soll schreiben alle minute welche sybmole auf dem radar"

Adds a `minute_heartbeat` async task in Bot.run() that:
  - Fires every 60s
  - Pushes ntfy via self.alerter.send("info", "🟢 HEARTBEAT t=Nm",
    body with current watchlist, bars_received, positions, day_pnl,
    last_ws_bar_ts, last_no_trade_reason)
  - Logs HEARTBEAT line to bot.log every minute

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


def test_minute_heartbeat_task_defined():
    src = _bot_src()
    assert "async def minute_heartbeat(" in src
    assert "minute_heartbeat started" in src


def test_heartbeat_interval_60s_or_15min():
    """Phase-91 was 60s; Phase-94 bumped to 900s (15 min) at user
    request to reduce phone-spam. Either value valid; we just forbid
    the never-sleeping case OR > 30min stale silence."""
    src = _bot_src()
    import re
    block = re.search(
        r"minute_heartbeat\(\)[\s\S]{0,3000}?await asyncio\.sleep\(\s*(\d+)\s*\)",
        src,
    )
    assert block, "minute_heartbeat must call asyncio.sleep"
    seconds = int(block.group(1))
    assert 60 <= seconds <= 1800, (
        f"heartbeat interval {seconds}s out of range "
        f"[60s, 1800s=30min]"
    )


def test_heartbeat_includes_watchlist():
    """ntfy body must list current watchlist symbols."""
    src = _bot_src()
    # Look for watchlist sym extraction
    assert "watchlist" in _bot_src().lower()
    import re
    block = re.search(
        r"async def minute_heartbeat[\s\S]{0,2000}?self\.tickers",
        src,
    )
    assert block, "heartbeat must read self.tickers for watchlist symbols"


def test_heartbeat_pushes_via_alerter():
    """The heartbeat must call self.alerter.send to push ntfy."""
    src = _bot_src()
    import re
    block = re.search(
        r"minute_heartbeat[\s\S]{0,2000}?alerter\.send\(",
        src,
    )
    assert block, "minute_heartbeat must use alerter.send"


def test_heartbeat_body_has_required_fields():
    """Body must include: bars_received, positions_open, day_pnl,
    last_ws_bar_ts, last_no_trade_reason."""
    src = _bot_src()
    # Heartbeat block contents check
    import re
    block = re.search(
        r"async def minute_heartbeat[\s\S]{0,3000}?\)\s*$",
        src,
        re.MULTILINE,
    )
    # at least find the body assembly area
    hb_area = re.search(
        r"async def minute_heartbeat[\s\S]{0,2000}",
        src,
    )
    assert hb_area
    body = hb_area.group(0)
    assert "bars_received" in body
    assert "positions_open" in body or "n_pos" in body
    assert "realized_pnl" in body or "day_pnl" in body
    assert "last_ws_bar_ts" in body
    assert "last_no_trade" in body


def test_heartbeat_task_launched_and_cancelled():
    """The task must be created with asyncio.create_task AND cancelled
    on shutdown (in pending-cancel loop)."""
    src = _bot_src()
    assert "hb_task = asyncio.create_task(minute_heartbeat()" in src
    assert "hb_task.cancel()" in src


def test_heartbeat_logs_to_bot_log():
    """Beside ntfy, also write a HEARTBEAT log line each tick so
    bot.log shows heartbeat history without ntfy.sh access."""
    src = _bot_src()
    import re
    block = re.search(
        r"minute_heartbeat[\s\S]{0,2500}?log\.info\([\"\']HEARTBEAT",
        src,
    )
    assert block, "heartbeat tick must also log to bot.log"


def test_phase_91_comment_present():
    src = _bot_src()
    assert "Phase-91" in src


def test_bot_still_imports():
    import bot
    assert hasattr(bot, "Bot")
