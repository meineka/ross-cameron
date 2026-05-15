"""fetch_loop.py — Phase-64 (2026-05-16)

Persistent background loop that incrementally extends the 1m intraday
pilot dataset every 20 minutes.

Why: the existing `fetch_historical_range.py` is one-shot — pick 50
symbols, fetch their range, done. But the Cameron universe has ~1600+
candidate tickers and we want to grow the dataset gradually without:
  - hammering Alpaca during live trading hours
  - leaving the operator to babysit re-runs
  - re-fetching already-covered (symbol, day) pairs

This loop:
  1. Reads the full Cameron universe from candidates.parquet
  2. Maintains state at 06_live_bot/fetch_loop_state.json — which
     tickers have been processed in the current cycle
  3. Every 20 minutes:
     a. Picks the next BATCH_SIZE unprocessed tickers (default 25)
     b. Spawns fetch_historical_range.py for them across the full
        2025-01-02 to today range
     c. Persists progress (idempotent: a Ctrl-C mid-fetch is safe
        because fetch_historical_range.py is itself idempotent)
  4. When all tickers processed: marks cycle complete, sleeps until
     a refresh interval (default 7 days), then restarts.

Output lands in the SAME 04_backtest/data_pilot/intraday_1m_ext.parquet
the user requested.

Usage:
    # Default: 20-min cadence, 25 symbols per batch, full range 2025+2026
    python fetch_loop.py

    # Custom cadence + batch
    python fetch_loop.py --interval-min 10 --batch-size 10

    # One-shot dry run (no waiting, just process next batch)
    python fetch_loop.py --once

    # Re-start cycle from scratch
    python fetch_loop.py --reset-state
"""
from __future__ import annotations
import argparse
import json
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_DIR = ROOT / "04_backtest" / "data_pilot"
CANDIDATES_PARQUET = PILOT_DIR / "candidates.parquet"
STATE_PATH = HERE / "fetch_loop_state.json"
FETCHER_SCRIPT = HERE / "fetch_historical_range.py"
LOG_DIR = HERE  # fetch_loop.log lives next to the script

DEFAULT_INTERVAL_MIN = 20
DEFAULT_BATCH_SIZE = 25
DEFAULT_START_DATE = "2025-01-02"
DEFAULT_TIMEFRAME = "1m"
CYCLE_REFRESH_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_loop.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("fetch-loop")

# Graceful-shutdown flag — set by SIGINT/SIGTERM
_stop_requested = False


def _signal_handler(signum, frame):  # noqa: ARG001
    global _stop_requested
    log.info("signal %s received — finishing current batch then stopping",
              signum)
    _stop_requested = True


signal.signal(signal.SIGINT, _signal_handler)
try:
    signal.signal(signal.SIGTERM, _signal_handler)
except AttributeError:
    pass  # Windows lacks SIGTERM in some Python builds


def load_universe() -> list[str]:
    """Cameron candidate universe = unique tickers from candidates.parquet.
    Falls back to empty list if file missing. Sorted alphabetically so
    the loop is deterministic across restarts."""
    try:
        import pandas as pd
        if not CANDIDATES_PARQUET.exists():
            log.error("universe source missing: %s", CANDIDATES_PARQUET)
            return []
        df = pd.read_parquet(CANDIDATES_PARQUET, columns=["ticker"])
        return sorted(df["ticker"].dropna().astype(str).unique().tolist())
    except Exception as e:
        log.error("universe load failed: %s", e)
        return []


def load_state(path: Path | None = None) -> dict:
    """State schema:
      {
        "cycle_started_at": ISO,
        "processed_tickers": ["AAA", "BBB", ...],
        "last_batch_at": ISO | null,
        "batches_run": int,
        "cycle_completed_at": ISO | null
      }

    `path` resolves to the module-level STATE_PATH when None — read at
    CALL time, not import time, so tests can monkeypatch the constant.
    """
    if path is None:
        path = STATE_PATH
    if not path.exists():
        return _fresh_state()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("state parse failed (%s) — fresh start", e)
        return _fresh_state()


def _fresh_state() -> dict:
    return {
        "cycle_started_at": datetime.now(timezone.utc).isoformat(),
        "processed_tickers": [],
        "last_batch_at": None,
        "batches_run": 0,
        "cycle_completed_at": None,
    }


def save_state(state: dict, path: Path | None = None) -> None:
    if path is None:
        path = STATE_PATH
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def pick_next_batch(universe: list[str], processed: set[str],
                      batch_size: int) -> list[str]:
    """Next BATCH_SIZE tickers from universe NOT in processed."""
    return [t for t in universe if t not in processed][:batch_size]


def run_one_batch(symbols: list[str], *, start_date: str,
                    end_date: str, timeframe: str) -> int:
    """Spawn fetch_historical_range.py for the batch. Returns subprocess
    exit code (0 on success). Streams child stdout into the loop log."""
    if not symbols:
        log.warning("run_one_batch called with empty symbol list")
        return 0
    log.info("Spawning fetcher: %d symbols [%s..%s] %s",
              len(symbols), symbols[0], symbols[-1], timeframe)
    cmd = [
        sys.executable, str(FETCHER_SCRIPT),
        "--start", start_date, "--end", end_date,
        "--timeframe", timeframe,
        "--symbols", ",".join(symbols),
    ]
    try:
        # Stream child output into our log
        proc = subprocess.run(cmd, cwd=ROOT, timeout=3600,
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        for line in (proc.stdout or "").splitlines()[-20:]:
            log.info("[fetcher] %s", line)
        for line in (proc.stderr or "").splitlines()[-10:]:
            log.warning("[fetcher-err] %s", line)
        return proc.returncode
    except subprocess.TimeoutExpired:
        log.error("fetcher hit 1h timeout — aborting batch")
        return 124
    except Exception as e:
        log.error("fetcher subprocess raised: %s", e)
        return 1


def cycle_should_restart(state: dict, refresh_days: int) -> bool:
    """If the cycle was completed >refresh_days ago, restart."""
    ts = state.get("cycle_completed_at")
    if not ts:
        return False
    try:
        completed_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - completed_at).days
        return age_days >= refresh_days
    except Exception:
        return False


def run_loop(*, interval_min: int, batch_size: int,
              start_date: str, end_date: str, timeframe: str,
              once: bool = False,
              refresh_days: int = CYCLE_REFRESH_DAYS) -> int:
    """Main loop. Returns exit code."""
    interval_sec = interval_min * 60
    while not _stop_requested:
        universe = load_universe()
        if not universe:
            log.error("empty universe — sleeping 1h then retry")
            if once:
                return 1
            _sleep_interruptibly(3600)
            continue
        state = load_state()
        if cycle_should_restart(state, refresh_days):
            log.info("cycle is %dd+ old — restarting from scratch",
                      refresh_days)
            state = _fresh_state()
        processed = set(state.get("processed_tickers", []))
        batch = pick_next_batch(universe, processed, batch_size)
        if not batch:
            # Cycle complete
            if not state.get("cycle_completed_at"):
                state["cycle_completed_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                save_state(state)
                log.info(
                    "CYCLE COMPLETE — all %d universe tickers processed "
                    "in %d batches. Sleeping until refresh (%dd).",
                    len(universe), state.get("batches_run", 0),
                    refresh_days,
                )
            if once:
                return 0
            _sleep_interruptibly(interval_sec)
            continue

        # Run the batch
        t0 = time.monotonic()
        rc = run_one_batch(batch, start_date=start_date,
                             end_date=end_date, timeframe=timeframe)
        elapsed = time.monotonic() - t0
        if rc == 0:
            state["processed_tickers"] = sorted(
                list(processed.union(batch))
            )
            state["last_batch_at"] = datetime.now(timezone.utc).isoformat()
            state["batches_run"] = state.get("batches_run", 0) + 1
            save_state(state)
            remaining = len(universe) - len(state["processed_tickers"])
            log.info("Batch OK in %.0fs — %d processed, %d remaining "
                      "(batch %d)",
                      elapsed, len(state["processed_tickers"]),
                      remaining, state["batches_run"])
        else:
            log.warning("Batch failed (rc=%d) — NOT marking as processed, "
                          "will retry next tick", rc)

        if once:
            return 0
        if _stop_requested:
            break
        log.info("Sleeping %d min until next batch …", interval_min)
        _sleep_interruptibly(interval_sec)
    log.info("loop exiting cleanly")
    return 0


def _sleep_interruptibly(seconds: int) -> None:
    """Sleep in 1-second chunks so SIGINT can break out fast."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop_requested:
        time.sleep(min(1.0, end - time.monotonic()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval-min", type=int, default=DEFAULT_INTERVAL_MIN,
                     help=f"minutes between batches (default "
                          f"{DEFAULT_INTERVAL_MIN})")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                     help=f"symbols per batch (default {DEFAULT_BATCH_SIZE})")
    ap.add_argument("--start-date", default=DEFAULT_START_DATE,
                     help=f"fetch from this date (default "
                          f"{DEFAULT_START_DATE})")
    ap.add_argument("--end-date",
                     default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                     help="fetch up to this date (default today)")
    ap.add_argument("--timeframe", choices=["1m", "5m"],
                     default=DEFAULT_TIMEFRAME,
                     help=f"bar resolution (default {DEFAULT_TIMEFRAME})")
    ap.add_argument("--once", action="store_true",
                     help="process exactly one batch and exit")
    ap.add_argument("--reset-state", action="store_true",
                     help="delete state file before running")
    ap.add_argument("--refresh-days", type=int, default=CYCLE_REFRESH_DAYS,
                     help=f"days after cycle completion before restart "
                          f"(default {CYCLE_REFRESH_DAYS})")
    args = ap.parse_args()

    if args.reset_state and STATE_PATH.exists():
        STATE_PATH.unlink()
        log.info("state file reset: %s", STATE_PATH)
    return run_loop(
        interval_min=args.interval_min,
        batch_size=args.batch_size,
        start_date=args.start_date,
        end_date=args.end_date,
        timeframe=args.timeframe,
        once=args.once,
        refresh_days=args.refresh_days,
    )


if __name__ == "__main__":
    sys.exit(main())
