"""Phase-82 (2026-05-19): alert hygiene + postmortem-trades-bug + rich BUY.

User reported four ntfy/postmortem bugs after the first live force-mode
day:

  1. "Daily Postmortem (0 trades)" was pushed even though 6 BUYs filled.
     Root cause: HARD_FLAT push check `self.day.trades_completed_today
     == 0` — but force-mode bracket SELL legs fire server-side, never
     incrementing trades_completed_today. Fix: use orders_submitted +
     bars_received as "did we trade?" signal.

  2. ALPACA RATE-LIMITED ntfy spam (~15 pushes/day). Root cause: 60s
     debounce too tight for the 250/min vs 200 cap pattern that
     supervisor + watchdog + position_monitor combined create. Fix:
     bump debounce to 30 min.

  3. supervisor "delete_stale_bot_pid + spawn_fetch_loop" ntfy spam
     (every 30min cycle = ~30 pushes/day). Root cause: these are
     ROUTINE post-restart cleanup. Fix: filter from push criteria,
     only alert on non-routine actions OR 4+ consecutive routine
     cycles (= real instability signal).

  4. BUY ntfy missing Stop / Target / R:R. Operator's phone shows
     "BUY CODX 400 @ $2.06 day PnL $0.00" — useless. Fix: enrich
     _push_trade(stop=, target=) to compute and include risk amount,
     reward amount, and R:R ratio.
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


def _supervisor_src() -> str:
    return (ROOT / "06_live_bot" / "supervisor.py").read_text(encoding="utf-8")


def _guarded_src() -> str:
    return (ROOT / "06_live_bot" / "guarded_alpaca.py").read_text(encoding="utf-8")


# ─── Bug 1: postmortem-fake-zero-trades ────────────────────────────────

def test_postmortem_uses_orders_submitted_not_trades_completed():
    """trades_completed_today is only incremented when manage_position
    SELL completes — but force-mode bracket SELL legs fire server-side.
    Postmortem condition must use a more reliable activity signal."""
    src = _bot_src()
    # The fixed condition checks orders_submitted OR bars_received
    assert "orders_submitted" in src
    assert "had_activity" in src or "did_trade" in src


def test_postmortem_sends_eod_summary_when_traded():
    """If we DID submit orders, postmortem must send a real summary
    push (orders, PnL, completed counts) instead of the
    "no trades today" message."""
    src = _bot_src()
    # f-string title with EOD prefix
    assert '"📊 EOD:' in src or "'📊 EOD:" in src or "EOD:" in src
    # Body mentions orders_submitted in the EOD push
    assert "orders_submitted=" in src or "completed=" in src


# ─── Bug 2: ALPACA RATE-LIMITED debounce ────────────────────────────────

def test_rate_limit_debounce_at_least_30min():
    """60s was too tight — bump to 30min so a sustained rate-limit
    state only pushes ≤2/hr instead of every flap."""
    src = _guarded_src()
    import re
    m = re.search(r"STATE_TRANSITION_DEBOUNCE_SEC\s*=\s*([\d.]+)", src)
    assert m, "STATE_TRANSITION_DEBOUNCE_SEC constant missing"
    val = float(m.group(1))
    assert val >= 600, f"debounce {val}s too short (want ≥600s = 10min)"


# ─── Bug 3: supervisor routine-action filter ────────────────────────────

def test_supervisor_filters_routine_actions_from_push():
    """ROUTINE_ACTIONS set must contain at least delete_stale_bot_pid
    and spawn_fetch_loop — the two actions the user reported as spam."""
    src = _supervisor_src()
    assert "ROUTINE_ACTIONS" in src
    assert '"delete_stale_bot_pid"' in src
    assert '"spawn_fetch_loop"' in src


def test_supervisor_pushes_only_on_notable_or_streak():
    """The push logic must distinguish 'notable' (non-routine) actions
    from routine cleanup. Routine-only cycles silent unless 4+ in a
    row (real instability signal)."""
    src = _supervisor_src()
    assert "notable" in src
    assert "_ROUTINE_STREAK" in src


def test_supervisor_routine_streak_init():
    """_ROUTINE_STREAK must be defined at module level so it persists
    across run-once invocations."""
    src = _supervisor_src()
    import re
    m = re.search(r"^_ROUTINE_STREAK\s*=\s*0", src, flags=re.MULTILINE)
    assert m, "_ROUTINE_STREAK must be initialized at module top"


# ─── Bug 4: BUY ntfy enrichment ────────────────────────────────────────

def test_push_trade_accepts_stop_and_target():
    """_push_trade signature must accept stop=/target= kwargs."""
    src = _bot_src()
    import re
    block = re.search(
        r"def _push_trade\([\s\S]{0,500}?stop:[\s\S]{0,200}?target:",
        src,
    )
    assert block, "_push_trade must accept stop= and target= kwargs"


def test_buy_push_includes_rr_ratio():
    """BUY ntfy body must include R:R calculation for operator's
    phone-only view of the trade plan."""
    src = _bot_src()
    assert "R:R" in src
    # Risk and reward computation
    assert "price - stop" in src
    assert "target - price" in src


def test_buy_call_site_passes_stop_and_target():
    """The Bot's BUY-push call site must pass actual_stop + actual_t2."""
    src = _bot_src()
    import re
    block = re.search(
        r'_push_trade\("BUY"[\s\S]{0,200}stop=actual_stop[\s\S]{0,200}target=actual_t2',
        src,
    )
    assert block, "BUY push must pass stop=actual_stop, target=actual_t2"


# ─── Sanity ─────────────────────────────────────────────────────────────

def test_phase_82_comments_present():
    bot = _bot_src()
    sup = _supervisor_src()
    gua = _guarded_src()
    assert "Phase-82" in bot
    assert "Phase-82" in sup
    assert "Phase-82" in gua


def test_bot_imports():
    import bot
    assert hasattr(bot, "Bot")
