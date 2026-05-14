"""no_trade_postmortem.py — Phase-13 (ChatGPT-20:11 P1).

Produces a machine-readable diagnosis for a no-trade day so the operator
doesn't have to do log-archaeology after every dead-day. Reads the
artifacts the bot already writes (status.json, daemon.log, watchdog.log,
day-summary, trade-logger), computes derived liveness signals, and
emits `no_trade_postmortem_YYYYMMDD.json` alongside them.

CLI:
    python no_trade_postmortem.py                 # today, NY-local
    python no_trade_postmortem.py 2026-05-14      # explicit date
    python no_trade_postmortem.py --json          # print to stdout

Designed to never crash on missing inputs — every field falls back to
a "missing" / None value the JSON consumer can detect.
"""
from __future__ import annotations
import json
import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# All output paths are relative to 06_live_bot (where the bot writes its
# artifacts). Keep these names in sync with bot.py.
STATUS_JSON = HERE / "status.json"
DAEMON_LOG = HERE / "daemon.log"
WATCHDOG_LOG = HERE / "watchdog.log"
TRADES_LIVE_JSONL = HERE / "trades_live.jsonl"
TRADES_REPLAY_JSONL = HERE / "trades_replay.jsonl"
DAY_SUMMARY_DIR = HERE  # day_summary_persist writes here by default


def _safe_read_json(path: Path) -> tuple[dict | None, str | None]:
    """Return (parsed-json, error). Both None means file missing."""
    if not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _tail_text(path: Path, max_bytes: int = 200_000) -> str:
    """Best-effort tail of a text file. Returns "" on missing/error."""
    if not path.exists():
        return ""
    try:
        sz = path.stat().st_size
        with path.open("rb") as f:
            if sz > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _is_pid_alive(pid: int) -> bool:
    """Cross-platform pid liveness check that doesn't depend on psutil."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(h)
            # STILL_ACTIVE = 259
            return bool(ok) and exit_code.value == 259
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False


def _find_bot_daemon_pids() -> list[int]:
    """Best-effort: find python.exe processes running bot.py --daemon."""
    pids: list[int] = []
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "ProcessId,CommandLine", "/format:csv"],
                text=True, timeout=10,
            )
            for line in out.splitlines():
                if "bot.py" in line and "--daemon" in line:
                    parts = line.rsplit(",", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        pids.append(int(parts[1].strip()))
        except Exception:
            pass
    return pids


def _find_watchdog_pids() -> list[int]:
    pids: list[int] = []
    if os.name == "nt":
        try:
            import subprocess
            out = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "ProcessId,CommandLine", "/format:csv"],
                text=True, timeout=10,
            )
            for line in out.splitlines():
                if "watchdog.py" in line:
                    parts = line.rsplit(",", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        pids.append(int(parts[1].strip()))
        except Exception:
            pass
    return pids


_WD_ERROR_RX = re.compile(r"\bERROR\b.*", re.IGNORECASE)
_LOG_LINE_RX = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def _extract_last_error(text: str) -> str | None:
    """Last line matching ERROR/WARNING substring (most-recent first)."""
    if not text:
        return None
    for line in reversed(text.splitlines()):
        if "ERROR" in line or "error" in line.lower():
            return line.strip()[:500]
    return None


def _extract_last_match(text: str, needle: str) -> str | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        if needle in line:
            return line.strip()[:500]
    return None


def _extract_pattern_counts_from_summary(summary: dict | None) -> dict:
    """Pull pattern-reject counters from a day-summary if present."""
    if not isinstance(summary, dict):
        return {}
    out = {}
    for k, v in summary.items():
        if k.startswith("patterns_rejected_") or k.startswith("reject_"):
            out[k] = v
    return out


def _count_orders_in_live_log(date_str: str) -> int:
    """Count entry events in trades_live.jsonl for the given date.
    `date_str` is YYYY-MM-DD; matches the prefix of the event ts."""
    if not TRADES_LIVE_JSONL.exists():
        return 0
    cnt = 0
    try:
        for line in TRADES_LIVE_JSONL.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ts = str(ev.get("ts", ""))
            if not ts.startswith(date_str):
                continue
            if ev.get("event") in {"entry", "buy", "T1", "T2_exit", "stop_exit",
                                     "quick_exit", "macd_exit"}:
                cnt += 1
    except OSError:
        return 0
    return cnt


def build_postmortem(target_date: str | None = None) -> dict:
    """Return the no-trade-postmortem dict for `target_date` (YYYY-MM-DD).
    Defaults to today in America/New_York if None.
    """
    if target_date is None:
        ny = timezone(timedelta(hours=-4))  # rough NY (DST handled elsewhere)
        target_date = datetime.now(ny).strftime("%Y-%m-%d")

    now_utc = datetime.now(timezone.utc)

    # 1) status.json
    status, status_err = _safe_read_json(STATUS_JSON)
    status_ts = None
    status_stale_seconds = None
    if status and isinstance(status, dict):
        try:
            t = status.get("ts")
            if isinstance(t, str):
                # Accept both naive and tz-aware ISO strings
                t_clean = t.replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(t_clean)
                except ValueError:
                    dt = None
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    status_ts = dt.isoformat()
                    status_stale_seconds = int((now_utc - dt).total_seconds())
        except Exception:
            pass

    # 2) Daemon + watchdog liveness
    bot_pids = _find_bot_daemon_pids()
    watchdog_pids = _find_watchdog_pids()
    bot_daemon_alive = any(_is_pid_alive(p) for p in bot_pids)
    watchdog_alive = any(_is_pid_alive(p) for p in watchdog_pids)

    # 3) Log scrapes
    daemon_text = _tail_text(DAEMON_LOG)
    watchdog_text = _tail_text(WATCHDOG_LOG)
    last_watchdog_error = _extract_last_error(watchdog_text)
    last_bot_start = _extract_last_match(daemon_text, "Started bot.py") \
                     or _extract_last_match(watchdog_text, "Started bot.py")
    last_ws_subscription = _extract_last_match(daemon_text, "WS subscribed") \
                            or _extract_last_match(daemon_text, "subscribe")
    last_scan_time = _extract_last_match(daemon_text, "scan") \
                      or _extract_last_match(daemon_text, "Scanner")

    # 4) Day-summary (if present)
    summary_paths = [
        DAY_SUMMARY_DIR / f"day_summary_{target_date}.json",
        DAY_SUMMARY_DIR / f"summary_{target_date}.json",
    ]
    summary = None
    for sp in summary_paths:
        s, _ = _safe_read_json(sp)
        if s is not None:
            summary = s
            break
    pattern_reject_counts = _extract_pattern_counts_from_summary(summary)

    pre_rank_candidates = None
    if isinstance(summary, dict):
        for k in ("pre_rank_candidates", "candidates_pre_rank", "candidates"):
            if k in summary:
                pre_rank_candidates = summary[k]
                break

    watchlist = None
    if isinstance(status, dict) and isinstance(status.get("watchlist"), list):
        watchlist = status["watchlist"]

    # 5) Orders submitted today (best-effort)
    orders_submitted = _count_orders_in_live_log(target_date)

    # 6) Final reason synthesis
    final_reason = _synthesize_final_reason(
        bot_daemon_alive, watchdog_alive, last_watchdog_error,
        status_stale_seconds, orders_submitted, pre_rank_candidates,
    )

    return {
        "schema_version": 1,
        "generated_at_utc": now_utc.isoformat(),
        "target_date_ny": target_date,
        "bot_daemon_alive": bot_daemon_alive,
        "bot_daemon_pids": bot_pids,
        "watchdog_alive": watchdog_alive,
        "watchdog_pids": watchdog_pids,
        "last_watchdog_error": last_watchdog_error,
        "last_bot_start": last_bot_start,
        "last_ws_subscription": last_ws_subscription,
        "status_json_ts": status_ts,
        "status_json_stale_seconds": status_stale_seconds,
        "status_json_parse_error": status_err,
        "last_scan_time": last_scan_time,
        "pre_rank_candidates": pre_rank_candidates,
        "watchlist": watchlist,
        "reject_counts_by_reason": pattern_reject_counts,
        "pattern_reject_counts": pattern_reject_counts,
        "orders_submitted": orders_submitted,
        "final_reason_no_trade": final_reason,
    }


def _synthesize_final_reason(bot_alive, wd_alive, wd_err, stale_sec,
                              orders, candidates) -> str:
    """Pick the most-actionable single line from the available signals."""
    if orders and orders > 0:
        return f"orders_submitted={orders} (NOT a no-trade day)"
    if not bot_alive:
        if wd_err:
            return f"bot_daemon_dead; watchdog last error: {wd_err}"
        if not wd_alive:
            return "bot_daemon_dead and watchdog_dead — nothing was running"
        return "bot_daemon_dead despite watchdog running — restart blocked"
    if stale_sec is not None and stale_sec > 1800:
        return f"bot_daemon_alive but status.json stale {stale_sec}s — possible hang"
    if candidates is not None and candidates == 0:
        return "bot_daemon_alive, scan produced 0 pre-rank candidates"
    if isinstance(candidates, int) and candidates > 0:
        return (f"bot_daemon_alive, {candidates} pre-rank candidates, "
                "but no pattern fired (see reject_counts_by_reason)")
    return "bot_daemon_alive but signals inconclusive — manual review needed"


def write_postmortem(target_date: str | None = None,
                      out_path: Path | None = None) -> Path:
    """Build + write the postmortem JSON, return the path."""
    doc = build_postmortem(target_date)
    if out_path is None:
        d = doc["target_date_ny"].replace("-", "")
        out_path = HERE / f"no_trade_postmortem_{d}.json"
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("date", nargs="?", default=None,
                    help="Target date YYYY-MM-DD (default: today NY-local)")
    p.add_argument("--json", action="store_true",
                    help="Print JSON to stdout instead of writing a file")
    args = p.parse_args(argv)
    if args.json:
        print(json.dumps(build_postmortem(args.date), indent=2))
        return 0
    out = write_postmortem(args.date)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
