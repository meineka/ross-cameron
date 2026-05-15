"""Phase-11 (ChatGPT-18:40 P0.2): live and replay trade logs MUST be
separated so trades_live.jsonl is a trustworthy live-ledger.

Asserts:
  - TradeLogger() defaults to trades_live.jsonl (live Bot behavior).
  - TradeLogger(filename=...) honors a custom filename.
  - TradeLogger(path=...) honors an explicit full path.
  - ReplayBot writes to trades_replay.jsonl, NOT trades_live.jsonl.
  - ReplayBot(log_path=False) writes nothing (null logger for tests).
"""
from __future__ import annotations
import sys, json
from pathlib import Path
import pytest


pytestmark = pytest.mark.critical  # Phase-19 (ChatGPT-08:49 #1): smoke/critical gate
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def test_tradelogger_default_path_is_live_ledger(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger()
    assert tl.path.name == "trades_live.jsonl"
    assert tl.path.parent == tmp_path


def test_tradelogger_filename_override(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    tl = bot.TradeLogger(filename="trades_replay.jsonl")
    assert tl.path.name == "trades_replay.jsonl"
    assert tl.path.parent == tmp_path


def test_tradelogger_explicit_path_wins(tmp_path):
    import bot
    custom = tmp_path / "custom.jsonl"
    tl = bot.TradeLogger(path=custom)
    assert tl.path == custom


def test_replaybot_default_writes_to_replay_log(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    rb = bot.ReplayBot()
    assert rb.logger.path.name == "trades_replay.jsonl", \
        "ReplayBot must default to trades_replay.jsonl (NOT trades_live.jsonl)"


def test_replaybot_does_not_pollute_live_ledger(tmp_path, monkeypatch):
    """The critical guarantee: write through ReplayBot's logger and
    confirm trades_live.jsonl is untouched."""
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    rb = bot.ReplayBot()
    rb.logger.log({"event": "REPLAY_entry", "symbol": "AAA"})
    live = tmp_path / "trades_live.jsonl"
    replay = tmp_path / "trades_replay.jsonl"
    assert not live.exists(), \
        "trades_live.jsonl must NOT be created by a ReplayBot write"
    assert replay.exists()
    line = json.loads(replay.read_text().strip())
    assert line["event"] == "REPLAY_entry"
    assert line["symbol"] == "AAA"


def test_replaybot_log_path_false_disables_persistence(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    rb = bot.ReplayBot(log_path=False)
    # NullTradeLogger has no .path
    assert isinstance(rb.logger, bot._NullTradeLogger)
    rb.logger.log({"event": "REPLAY_entry"})
    assert not (tmp_path / "trades_live.jsonl").exists()
    assert not (tmp_path / "trades_replay.jsonl").exists()


def test_replaybot_explicit_log_path_override(tmp_path, monkeypatch):
    import bot
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    custom = tmp_path / "scratch.jsonl"
    rb = bot.ReplayBot(log_path=custom)
    assert rb.logger.path == custom
    rb.logger.log({"event": "REPLAY_entry"})
    assert custom.exists()
