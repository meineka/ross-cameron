"""Phase-30: trade-event push notifications.

Verify that every entry-fill and every exit-fill triggers exactly one
push via the bot's alerter, with the correct symbol/shares/price in the
title and the running day-PnL in the body.

The tests instantiate Bot via __new__ to avoid touching Alpaca client
init, then call _push_trade directly with a recording alerter. This is
fine because we're verifying the contract of _push_trade and the
helper's input from each call-site (covered by source-grep tests below).
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _make_bot_with_recorder():
    """Bot stub with a recording alerter. Bypasses Alpaca init."""
    import bot
    b = bot.Bot.__new__(bot.Bot)
    b.day = bot.DayState()
    b.alerter = MagicMock()
    b.alerter.send.return_value = True
    return b


def test_push_trade_entry_emits_buy_alert():
    b = _make_bot_with_recorder()
    b._push_trade("BUY", "MEGA", 100, 4.52)
    assert b.alerter.send.call_count == 1
    call = b.alerter.send.call_args
    args, kwargs = call.args, call.kwargs
    level, title, body = args[0], args[1], args[2]
    assert level == "info"
    assert title == "BUY MEGA 100 @ $4.52"
    assert "day PnL" in body
    assert kwargs.get("force") is True  # bypasses 5-min debounce


def test_push_trade_winning_exit_emits_info_with_plus_sign():
    b = _make_bot_with_recorder()
    b.day.realized_pnl = 142.0  # already-won today
    b._push_trade("T2", "MEGA", 100, 5.20, pnl=68.0)
    args, kwargs = b.alerter.send.call_args.args, b.alerter.send.call_args.kwargs
    assert args[0] == "info"
    assert args[1] == "T2 MEGA 100 @ $5.20 PnL +$68.00"
    # day-PnL is rendered via {:+.2f} → "$+142.00"
    assert "$+142.00" in args[2]
    assert kwargs.get("force") is True


def test_push_trade_losing_exit_uses_warn_level():
    b = _make_bot_with_recorder()
    b.day.realized_pnl = -10.5
    b._push_trade("STOP", "BADX", 50, 4.30, pnl=-10.5)
    args = b.alerter.send.call_args.args
    assert args[0] == "warn"
    assert "PnL $-10.50" in args[1]
    assert "$-10.50" in args[2]


def test_push_trade_swallows_alerter_failure():
    """If alerter.send raises, _push_trade must NOT crash the bot."""
    b = _make_bot_with_recorder()
    b.alerter.send.side_effect = RuntimeError("network down")
    # Must not raise
    b._push_trade("BUY", "MEGA", 100, 4.52)


def test_push_trade_handles_none_alerter():
    """If make_alerter() failed during init, alerter is None — no push,
    no crash."""
    import bot
    b = bot.Bot.__new__(bot.Bot)
    b.day = bot.DayState()
    b.alerter = None
    # Must not raise
    b._push_trade("BUY", "MEGA", 100, 4.52)
    b._push_trade("T2", "MEGA", 100, 5.20, pnl=68.0)


# ─── Source-level wiring checks — every fill path calls _push_trade ────────

def test_entry_fill_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # After self.logger.log({...event":"entry"...}) there must be a push
    entry_idx = src.find('"event": "entry"')
    assert entry_idx > 0
    tail = src[entry_idx: entry_idx + 800]
    assert '_push_trade("BUY"' in tail, "entry-fill must push BUY alert"


def test_macd_exit_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find('  MACD-EXIT %s @')
    assert idx > 0
    tail = src[idx: idx + 400]
    assert '_push_trade("MACD"' in tail


def test_quick_exit_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find('  QUICK-EXIT %s @')
    assert idx > 0
    tail = src[idx: idx + 400]
    assert '_push_trade("QUICK"' in tail


def test_t1_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find('"event": "T1"')
    assert idx > 0
    tail = src[idx: idx + 500]
    assert '_push_trade("T1"' in tail


def test_t2_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find('"event": "T2_exit"')
    assert idx > 0
    # Window must cover the success branch which falls through past
    # _check_daily_goal — extend to ~900 chars.
    tail = src[idx: idx + 900]
    assert '_push_trade("T2"' in tail


def test_stop_path_calls_push_trade():
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    idx = src.find('"event": "stop_exit"')
    assert idx > 0
    tail = src[idx: idx + 500]
    # STOP or BE — either branch acceptable
    assert '_push_trade(kind' in tail or '_push_trade("STOP"' in tail


def test_bot_init_wires_alerter():
    """Bot.__init__ must instantiate make_alerter()."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from alerter import make_alerter" in src
    assert "self.alerter = make_alerter()" in src
