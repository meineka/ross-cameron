"""Phase-64: periodic backtest-data fetcher loop.

The loop runs every N minutes in background, picks the next batch of
unprocessed Cameron-universe tickers, spawns fetch_historical_range.py
for them, and persists progress. These tests lock in:

  - batch picker advances through the universe deterministically
  - state file survives Ctrl-C mid-batch (idempotent restart)
  - completed cycle triggers refresh after refresh_days
  - --once exits after one batch
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── 1. Batch picker ─────────────────────────────────────────────────────

def test_pick_next_batch_returns_first_n_unprocessed():
    from fetch_loop import pick_next_batch
    universe = ["A", "B", "C", "D", "E"]
    batch = pick_next_batch(universe, processed=set(), batch_size=3)
    assert batch == ["A", "B", "C"]


def test_pick_next_batch_skips_already_processed():
    from fetch_loop import pick_next_batch
    universe = ["A", "B", "C", "D", "E"]
    batch = pick_next_batch(universe, processed={"A", "C"}, batch_size=3)
    assert batch == ["B", "D", "E"]


def test_pick_next_batch_returns_empty_when_done():
    from fetch_loop import pick_next_batch
    universe = ["A", "B", "C"]
    batch = pick_next_batch(universe, processed={"A", "B", "C"},
                              batch_size=10)
    assert batch == []


def test_pick_next_batch_clips_to_remaining():
    """If only 2 unprocessed left but batch_size=5, returns 2."""
    from fetch_loop import pick_next_batch
    universe = ["A", "B", "C", "D", "E"]
    batch = pick_next_batch(universe, processed={"A", "B", "C"},
                              batch_size=5)
    assert batch == ["D", "E"]


# ─── 2. State save/load roundtrip ────────────────────────────────────────

def test_save_load_state_roundtrip(tmp_path):
    from fetch_loop import save_state, load_state, _fresh_state
    state = _fresh_state()
    state["processed_tickers"] = ["AAA", "BBB"]
    state["batches_run"] = 3
    p = tmp_path / "state.json"
    save_state(state, p)
    loaded = load_state(p)
    assert loaded["processed_tickers"] == ["AAA", "BBB"]
    assert loaded["batches_run"] == 3


def test_load_state_returns_fresh_when_missing(tmp_path):
    from fetch_loop import load_state
    state = load_state(tmp_path / "absent.json")
    assert state["processed_tickers"] == []
    assert state["batches_run"] == 0
    assert state["cycle_completed_at"] is None


def test_load_state_returns_fresh_when_corrupt(tmp_path):
    from fetch_loop import load_state
    p = tmp_path / "broken.json"
    p.write_text("{ not valid json", encoding="utf-8")
    state = load_state(p)
    assert state["processed_tickers"] == []


def test_save_state_uses_atomic_tmp_rename(tmp_path):
    """Crash-safety: state file is written via .tmp then replace, so a
    crash mid-write never leaves a partial file."""
    from fetch_loop import save_state, _fresh_state
    p = tmp_path / "state.json"
    save_state(_fresh_state(), p)
    assert p.exists()
    # No leftover tmp file
    assert not (tmp_path / "state.json.tmp").exists()


# ─── 3. Cycle refresh logic ──────────────────────────────────────────────

def test_cycle_should_restart_returns_false_when_not_completed():
    from fetch_loop import cycle_should_restart
    state = {"cycle_completed_at": None}
    assert cycle_should_restart(state, refresh_days=7) is False


def test_cycle_should_restart_returns_false_when_recent():
    from fetch_loop import cycle_should_restart
    state = {
        "cycle_completed_at": datetime.now(timezone.utc).isoformat()
    }
    assert cycle_should_restart(state, refresh_days=7) is False


def test_cycle_should_restart_returns_true_when_old():
    from fetch_loop import cycle_should_restart
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    state = {"cycle_completed_at": old}
    assert cycle_should_restart(state, refresh_days=7) is True


def test_cycle_should_restart_handles_corrupt_timestamp():
    from fetch_loop import cycle_should_restart
    state = {"cycle_completed_at": "not-a-timestamp"}
    assert cycle_should_restart(state, refresh_days=7) is False


# ─── 4. run_one_batch subprocess wrapper ────────────────────────────────

def test_run_one_batch_short_circuits_on_empty_symbols(monkeypatch):
    """Defensive: no symbols → return 0 without spawning subprocess."""
    import fetch_loop
    spawned = []

    def _fake_run(*a, **kw):
        spawned.append(a)
        raise AssertionError("should not be called")

    monkeypatch.setattr(fetch_loop.subprocess, "run", _fake_run)
    rc = fetch_loop.run_one_batch([], start_date="2025-01-02",
                                     end_date="2025-12-31",
                                     timeframe="1m")
    assert rc == 0
    assert spawned == []


def test_run_one_batch_returns_subprocess_returncode(monkeypatch):
    """Forwards child exit code so caller can decide retry policy."""
    import fetch_loop
    from unittest.mock import MagicMock
    fake_proc = MagicMock(returncode=42, stdout="", stderr="")
    monkeypatch.setattr(fetch_loop.subprocess, "run",
                          lambda *a, **kw: fake_proc)
    rc = fetch_loop.run_one_batch(["AAA"], start_date="2025-01-02",
                                     end_date="2025-12-31",
                                     timeframe="1m")
    assert rc == 42


def test_run_one_batch_returns_124_on_timeout(monkeypatch):
    """Subprocess.TimeoutExpired → return 124 (standard timeout code)."""
    import fetch_loop
    import subprocess as _sp

    def _raise_timeout(*a, **kw):
        raise _sp.TimeoutExpired(cmd="x", timeout=10)

    monkeypatch.setattr(fetch_loop.subprocess, "run", _raise_timeout)
    rc = fetch_loop.run_one_batch(["AAA"], start_date="2025-01-02",
                                     end_date="2025-12-31",
                                     timeframe="1m")
    assert rc == 124


# ─── 5. End-to-end --once mode ───────────────────────────────────────────

def test_run_loop_once_processes_one_batch_then_exits(monkeypatch,
                                                         tmp_path):
    """Headline integration: --once spawns 1 batch and exits."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fetch_loop, "load_universe",
                          lambda: ["A", "B", "C", "D", "E"])
    batches_run = []

    def _fake_batch(symbols, **kw):
        batches_run.append(list(symbols))
        return 0

    monkeypatch.setattr(fetch_loop, "run_one_batch", _fake_batch)
    rc = fetch_loop.run_loop(
        interval_min=1, batch_size=3,
        start_date="2025-01-02", end_date="2025-12-31",
        timeframe="1m", once=True,
    )
    assert rc == 0
    assert len(batches_run) == 1
    assert batches_run[0] == ["A", "B", "C"]
    # State persisted with first batch as processed
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(state["processed_tickers"]) == {"A", "B", "C"}
    assert state["batches_run"] == 1


def test_run_loop_once_advances_through_universe_across_calls(monkeypatch,
                                                                  tmp_path):
    """Idempotent: 3 sequential --once calls process disjoint batches
    until the universe is exhausted."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fetch_loop, "load_universe",
                          lambda: ["A", "B", "C", "D", "E"])
    batches_run = []

    def _fake_batch(symbols, **kw):
        batches_run.append(list(symbols))
        return 0

    monkeypatch.setattr(fetch_loop, "run_one_batch", _fake_batch)
    for _ in range(3):
        fetch_loop.run_loop(interval_min=1, batch_size=2,
                              start_date="2025-01-02", end_date="2025-12-31",
                              timeframe="1m", once=True)
    # Two batches actually have work, third sees empty + marks complete
    assert batches_run == [["A", "B"], ["C", "D"], ["E"]]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(state["processed_tickers"]) == {"A", "B", "C", "D", "E"}


def test_run_loop_failed_batch_does_not_mark_processed(monkeypatch,
                                                          tmp_path):
    """If the subprocess returns non-zero, we DON'T mark the symbols as
    done — next iteration retries them. Critical for transient Alpaca
    errors not silently leaving gaps in the dataset. We verify this by
    running a SECOND --once tick and confirming it picks the SAME
    symbols (i.e. they were not persisted as processed)."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fetch_loop, "load_universe",
                          lambda: ["A", "B", "C"])
    batches_attempted = []

    def _fail_first_succeed_second(symbols, **kw):
        batches_attempted.append(list(symbols))
        return 1 if len(batches_attempted) == 1 else 0

    monkeypatch.setattr(fetch_loop, "run_one_batch",
                          _fail_first_succeed_second)
    # Tick 1: failure
    fetch_loop.run_loop(interval_min=1, batch_size=2,
                          start_date="2025-01-02", end_date="2025-12-31",
                          timeframe="1m", once=True)
    # Tick 2: same symbols retried, this time succeed
    fetch_loop.run_loop(interval_min=1, batch_size=2,
                          start_date="2025-01-02", end_date="2025-12-31",
                          timeframe="1m", once=True)
    # Both attempts went after [A, B] — confirms failure didn't mark them
    assert batches_attempted == [["A", "B"], ["A", "B"]]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert set(state["processed_tickers"]) == {"A", "B"}
    assert state["batches_run"] == 1  # only the succeeding attempt counts


def test_run_loop_completed_cycle_marks_completion(monkeypatch, tmp_path):
    """After the last batch, the next --once tick marks cycle_completed_at
    so the refresh logic can kick in later."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fetch_loop, "load_universe", lambda: ["A", "B"])
    monkeypatch.setattr(fetch_loop, "run_one_batch",
                          lambda s, **kw: 0)
    # Two passes: first processes [A,B], second sees empty → marks complete
    for _ in range(2):
        fetch_loop.run_loop(interval_min=1, batch_size=10,
                              start_date="2025-01-02",
                              end_date="2025-12-31",
                              timeframe="1m", once=True)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["cycle_completed_at"] is not None
    assert set(state["processed_tickers"]) == {"A", "B"}


def test_run_loop_restarts_cycle_when_state_is_stale(monkeypatch, tmp_path):
    """Cycle completed 10 days ago + refresh_days=7 → fresh start."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    # Pre-seed state with old completed cycle
    old_state = {
        "cycle_started_at": (datetime.now(timezone.utc)
                              - timedelta(days=20)).isoformat(),
        "processed_tickers": ["A", "B", "C"],
        "last_batch_at": (datetime.now(timezone.utc)
                            - timedelta(days=10)).isoformat(),
        "batches_run": 5,
        "cycle_completed_at": (datetime.now(timezone.utc)
                                 - timedelta(days=10)).isoformat(),
    }
    (tmp_path / "state.json").write_text(json.dumps(old_state),
                                            encoding="utf-8")
    monkeypatch.setattr(fetch_loop, "load_universe",
                          lambda: ["A", "B", "C"])
    batches_run = []
    monkeypatch.setattr(fetch_loop, "run_one_batch",
                          lambda s, **kw: (batches_run.append(s) or 0))
    fetch_loop.run_loop(interval_min=1, batch_size=2,
                          start_date="2025-01-02", end_date="2025-12-31",
                          timeframe="1m", once=True, refresh_days=7)
    # Fresh cycle started — A,B re-fetched (because old state thrown out)
    assert batches_run == [["A", "B"]]


def test_run_loop_empty_universe_exits_with_1(monkeypatch, tmp_path):
    """Defensive: no universe → in --once mode, return 1 (error)."""
    import fetch_loop
    monkeypatch.setattr(fetch_loop, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(fetch_loop, "load_universe", lambda: [])
    rc = fetch_loop.run_loop(interval_min=1, batch_size=10,
                                start_date="2025-01-02",
                                end_date="2025-12-31",
                                timeframe="1m", once=True)
    assert rc == 1
