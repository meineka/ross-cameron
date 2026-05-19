"""Phase-76: WS-storm zombie-thread fix (2026-05-19).

Today's incident:
  17:39:43 — log shows 8 PARALLEL "starting data websocket connection"
             entries within the SAME millisecond
  17:00-18:00 — 991 WS-connection attempts in one hour (~16/min)
  All hit TimeoutError or ConnectionClosedError
  User: "warum heute wieder keine trades"

Root cause: bot.py ws_loop used `asyncio.to_thread(ws.run)`. When the
asyncio task got cancelled via wait_for-timeout-cancel, the asyncio
TASK was cancelled but the underlying OS THREAD kept running ws.run().
Next iteration created ANOTHER asyncio.to_thread call → ANOTHER OS
thread → both calling ws.run() on the Phase-43 SINGLETON instance.
The singleton enforces ONE construction but NOT one concurrent
_run_forever — so both threads tried to connect to Alpaca's WS in
parallel → "connection limit exceeded" or TimeoutError cascade.

Fix:
  1. Replace asyncio.to_thread(ws.run) with an explicit
     threading.Thread(target=ws.run) stored on self._ws_run_thread.
  2. At top of each ws_loop iteration, check
     self._ws_run_thread.is_alive() — if True, SKIP this iteration
     (zombie from previous cancel still running).
  3. ws_task now wraps join-wait, not the run itself.

These tests pin the contract via source-grep.
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


# ─── 1. Zombie-check at top of iteration ────────────────────────────────

def test_ws_loop_checks_prev_thread_is_alive():
    """Before starting a new ws.run thread, we must check the previous
    one is dead. Otherwise zombies pile up."""
    src = _bot_src()
    assert "_ws_run_thread" in src
    assert "is_alive()" in src
    # Must be in the ws_loop area, not just imported
    assert "prev_thread.is_alive()" in src or "_ws_run_thread.is_alive()" in src


def test_ws_loop_skips_iteration_when_prev_thread_alive():
    """If prev thread alive, must `continue` (skip) instead of spawning
    a competing one."""
    src = _bot_src()
    # The skip pattern: log warning + sleep + continue
    assert "skipping new spawn" in src or "zombie-stack" in src
    # The continue keyword must be present in the alive-check branch
    import re
    block = re.search(
        r"prev_thread\.is_alive\(\)[\s\S]{0,400}?continue",
        src,
    )
    assert block, "missing `continue` after is_alive check"


# ─── 2. Use threading.Thread, not asyncio.to_thread for ws.run ──────────

def test_ws_run_uses_owned_thread_not_to_thread():
    """asyncio.to_thread(ws.run) is the BUG — the thread can't be
    cancelled by asyncio.task.cancel(). Replaced with
    threading.Thread(target=ws.run)."""
    src = _bot_src()
    # The fix uses threading.Thread for ws.run
    import re
    # threading.Thread with target=ws.run
    assert re.search(r"_th\.Thread\(\s*target=ws\.run", src), (
        "ws.run must be in a threading.Thread we own"
    )
    # asyncio.to_thread(ws.run) — the OLD bug — must NOT appear
    assert "asyncio.to_thread(ws.run)" not in src, (
        "asyncio.to_thread(ws.run) is the zombie-leak bug"
    )


def test_ws_run_thread_stored_on_self():
    """self._ws_run_thread is the cross-iteration handle for the
    is_alive check."""
    src = _bot_src()
    assert "self._ws_run_thread" in src
    # Should be a Thread object (storing thread, not task)
    assert "_th.Thread" in src or "threading.Thread" in src


# ─── 3. Phase-76 comment trail for archaeology ──────────────────────────

def test_phase_76_explanation_comment_present():
    """Future operator must understand WHY this convoluted pattern
    exists — comment must explain the zombie-thread mechanism."""
    src = _bot_src()
    assert "Phase-76" in src
    assert "zombie" in src.lower()
    assert "connection limit" in src.lower() or "991" in src


# ─── 4. ws_task semantics preserved ──────────────────────────────────────

def test_ws_task_still_used_for_wait_loop():
    """The outer ws_task is now a join-wait task — its done() signal
    still drives the resubscribe-watch loop semantics."""
    src = _bot_src()
    # ws_task still exists in some form
    assert "ws_task" in src
    # done() polling still drives the inner loop
    assert "ws_task.done()" in src or "ws_task.is_done()" in src


# ─── 5. Sanity: import doesn't break ─────────────────────────────────────

def test_bot_module_still_imports():
    """The simplest health check — bot.py syntactically valid + module
    imports cleanly with all the new threading code."""
    import bot
    assert hasattr(bot, "Bot")
    assert hasattr(bot, "TOP_N")
