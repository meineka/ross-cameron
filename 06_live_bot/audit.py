"""audit.py — Health-Audit von daemon.log, klassifiziert Errors.

Nutzung von Auto-Healer:
  python audit.py           # zeigt aktuelle Status + Error-Klassifizierung
  python audit.py --json    # maschinen-lesbar
"""
from __future__ import annotations
import sys, io, json, re, argparse
from pathlib import Path
from datetime import datetime, timedelta
import subprocess

HERE = Path(__file__).resolve().parent
LOG = HERE / "daemon.log"

# Error-Categories mit Auto-Fix-Hinweis
ERROR_PATTERNS = [
    # (regex, kategorie, severity, auto_fixable, fix_hint)
    (r"possibly delisted|Quote not found for symbol|no price data found|no timezone found|YFRateLimitError|\['[A-Z]+'\]:", "yfinance_delisted", "info", True, "Symbol delisted/yfinance noise"),
    (r"ERROR\s+\[yfinance\]\s*$", "yfinance_empty_line", "info", True, "yfinance log artifact"),
    (r"YFRateLimitError|429|Too Many Requests", "yfinance_rate_limit", "low", True, "wartet selbst"),
    (r"WS error.*'str' object has no attribute 'value'", "ws_api_drift", "critical", False, "alpaca-py erwartet DataFeed-Enum statt String"),
    (r"WS error|WebSocket.*disconnected", "ws_disconnect", "low", True, "auto-reconnect aktiv"),
    (r"Connection refused|Connection reset", "network", "medium", True, "wartet auf reconnect"),
    (r"Alpaca-Connection FAIL", "alpaca_auth", "high", False, "API-keys checken"),
    (r"insufficient.*buying power|insufficient.*funds", "no_buying_power", "high", False, "account drained?"),
    (r"asset.*not tradable|asset.*halted", "asset_not_tradable", "low", True, "skip stock"),
    (r"order.*rejected|405", "order_rejected", "medium", True, "log + skip"),
    (r"NameError|AttributeError|TypeError|ValueError", "code_bug", "critical", False, "code-fix nötig"),
    (r"KeyboardInterrupt", "user_stop", "info", False, "manual stop"),
    (r"NO CANDIDATES found", "empty_watchlist", "info", True, "Markt-Holiday möglicherweise"),
    (r"SPY-BEAR-DAY", "spy_bear", "info", False, "Bot schützt sich selbst"),
    (r"DAILY GOAL.*ERREICHT", "goal_reached", "info", False, "good day"),
    (r"SPIRAL-DETECTION", "spiral_lock", "warning", False, "2 losses, bot stoppt"),
    (r"max_5_trades_today", "max_trades", "info", False, "rate-limited"),
]


def get_recent_log_lines(minutes: int = 30) -> list[str]:
    """Lese Zeilen aus log die letzten N Minuten.

    Audit-Iter 29 (Bug AU-7): Multi-line tracebacks haben nur in der ersten
    Zeile einen Timestamp. Folge-Lines (mit File-Paths + Exception-Message)
    wurden silent gedroppt. Jetzt: wenn line keinen Timestamp hat, aber die
    VORHERIGE line in der Window war → mit-includen (= traceback-Erweiterung).
    """
    if not LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(minutes=minutes)
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    last_in_window = False
    for line in lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                last_in_window = ts >= cutoff
                if last_in_window:
                    out.append(line)
            except Exception:
                last_in_window = False
        elif last_in_window:
            # Traceback-Folgezeile (kein eigener Timestamp)
            out.append(line)
    return out


def classify_errors(lines: list[str]) -> list[dict]:
    """Klassifiziere Errors UND wichtige WARNINGs in den Lines.

    Audit-Bug-Fix 2026-05-12: WARNING-Lines wie SPIRAL-DETECTION / DAILY GOAL
    waren bisher unreachable für die info-Pattern. Jetzt werden sie auch matched.

    Audit-Iter 29 (Bug AU-5): pre-filter ließ INFO-Lines durchfallen, aber
    KeyboardInterrupt + NO CANDIDATES + DAILY GOAL werden teilweise als INFO
    geloggt. Jetzt: pre-filter checkt ZUSÄTZLICH ob die Line einen der
    ERROR_PATTERNS matched → INFO-Lines werden mit-erfasst wenn relevant.
    """
    findings = []
    for line in lines:
        is_error_like = (
            "ERROR" in line or "Traceback" in line
            or "FAIL" in line or "WARNING" in line or "CRITICAL" in line
        )
        if not is_error_like:
            # Audit-Iter 29 (AU-5): auch INFO-Lines wenn sie ein Pattern matchen
            if not any(re.search(p, line, re.IGNORECASE)
                       for p, *_ in ERROR_PATTERNS):
                continue
        for pattern, category, severity, fixable, hint in ERROR_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append({
                    "line": line,
                    "category": category,
                    "severity": severity,
                    "auto_fixable": fixable,
                    "fix_hint": hint,
                })
                break
        else:
            if is_error_like:
                # Nur unmatched ERROR/WARNING/FAIL als "unknown" tracken
                findings.append({
                    "line": line,
                    "category": "unknown",
                    "severity": "high",
                    "auto_fixable": False,
                    "fix_hint": "needs human review",
                })
    return findings


def _check_bot_alive_cross_platform() -> tuple[bool, int, int]:
    """Returns (alive, memory_kb, pid_count). Phase-18 wraps the new
    `_collect_bot_processes()` which also exposes PID + parent-PID for
    multi-instance classification."""
    procs = _collect_bot_processes()
    if not procs:
        return False, 0, 0
    mem_kb = sum(p.get("memory_kb", 0) for p in procs)
    return True, mem_kb, len(procs)


def _collect_bot_processes() -> list[dict]:
    """Phase-18 (ChatGPT-08:49 #5 P0): return one dict per python process
    running `bot.py --daemon`. Each dict has {pid, ppid, memory_kb}.
    Cross-platform (psutil → wmic → pgrep). Used by both the legacy
    bot_pid_count consumers and the new classify_bot_processes() gate."""
    procs: list[dict] = []
    # Try psutil first (cross-platform, accurate)
    try:
        import psutil
        for p in psutil.process_iter(["pid", "ppid", "cmdline", "memory_info"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "bot.py" in cmdline and "--daemon" in cmdline:
                    procs.append({
                        "pid": p.info.get("pid"),
                        "ppid": p.info.get("ppid"),
                        "memory_kb": (p.info["memory_info"].rss // 1024)
                                       if p.info.get("memory_info") else 0,
                    })
            except Exception:
                pass
        return procs
    except ImportError:
        pass
    # Fallback: OS-specific
    import os as _os
    if _os.name == "nt":
        # Windows: wmic with PPID
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "CommandLine,ParentProcessId,ProcessId,WorkingSetSize",
                 "/format:csv"],
                text=True, timeout=5,
            )
            for line in out.splitlines():
                if "bot.py" in line and "--daemon" in line:
                    # CSV columns: Node,CommandLine,ParentProcessId,ProcessId,WorkingSetSize
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) < 5:
                        continue
                    try:
                        ppid_s, pid_s, wss = parts[-3], parts[-2], parts[-1]
                        procs.append({
                            "pid": int(pid_s) if pid_s.isdigit() else None,
                            "ppid": int(ppid_s) if ppid_s.isdigit() else None,
                            "memory_kb": (int(wss) // 1024) if wss.isdigit() else 0,
                        })
                    except Exception:
                        pass
        except Exception:
            pass
    else:
        # Linux/Mac: pgrep + /proc for PPID
        try:
            out = subprocess.check_output(
                ["pgrep", "-af", "bot.py.*--daemon"],
                text=True, timeout=5,
            )
            for line in out.splitlines():
                parts = line.split(None, 1)
                if not parts or not parts[0].isdigit():
                    continue
                pid = int(parts[0])
                ppid = None
                rss = 0
                try:
                    rss = int(subprocess.check_output(
                        ["ps", "-o", "rss=", "-p", str(pid)],
                        text=True, timeout=2,
                    ).strip())
                except Exception:
                    pass
                try:
                    stat_line = subprocess.check_output(
                        ["ps", "-o", "ppid=", "-p", str(pid)],
                        text=True, timeout=2,
                    ).strip()
                    ppid = int(stat_line) if stat_line.isdigit() else None
                except Exception:
                    pass
                procs.append({"pid": pid, "ppid": ppid, "memory_kb": rss})
        except Exception:
            pass
    return procs


def classify_bot_processes(procs: list[dict] | None = None) -> dict:
    """Phase-18 (ChatGPT-08:49 #5 P0): classify the set of bot processes
    into exactly one of three categories:

      "none"                       → no bot.py --daemon running
      "single"                     → exactly one bot process
      "launcher_child_pair"        → two processes where one is the
                                      direct parent of the other
                                      (venv launcher + daemon child)
      "multiple_independent_bots"  → 2+ processes with no parent-child
                                      relationship between them → P0 FAIL

    Watchdog / deploy must REFUSE to start new instances when the
    classification is `multiple_independent_bots`. Returns:
      {"classification": str,
       "process_count": int,
       "pids": list[int],
       "process_pairs": list[{launcher, child}],
       "standalone_pids": list[int],
       "is_safe_to_restart": bool,
       "block_reason": str | None}
    """
    if procs is None:
        procs = _collect_bot_processes()
    pids = [p["pid"] for p in procs if p.get("pid") is not None]
    if len(pids) == 0:
        return {
            "classification": "none",
            "process_count": 0,
            "pids": [],
            "process_pairs": [],
            "standalone_pids": [],
            "is_safe_to_restart": True,
            "block_reason": None,
        }
    if len(pids) == 1:
        return {
            "classification": "single",
            "process_count": 1,
            "pids": pids,
            "process_pairs": [],
            "standalone_pids": pids,
            "is_safe_to_restart": True,
            "block_reason": None,
        }

    # Multiple — distinguish launcher/child pair from independent multi-bots
    pid_set = set(pids)
    pairs = []
    paired = set()
    for p in procs:
        pid = p.get("pid")
        ppid = p.get("ppid")
        if pid is None or ppid is None:
            continue
        if ppid in pid_set and ppid != pid:
            pairs.append({"launcher": ppid, "child": pid})
            paired.add(pid)
            paired.add(ppid)
    standalone = [p for p in pids if p not in paired]

    if len(pairs) >= 1 and len(standalone) == 0:
        # All accounted for via launcher/child pairs (typically just one)
        return {
            "classification": "launcher_child_pair",
            "process_count": len(pids),
            "pids": pids,
            "process_pairs": pairs,
            "standalone_pids": [],
            "is_safe_to_restart": True,
            "block_reason": None,
        }
    # Independent multi-bot — P0 fail
    return {
        "classification": "multiple_independent_bots",
        "process_count": len(pids),
        "pids": pids,
        "process_pairs": pairs,
        "standalone_pids": standalone,
        "is_safe_to_restart": False,
        "block_reason": (f"{len(standalone)} standalone bot process(es) detected — "
                         "refusing to spawn another. Manually flatten + kill duplicates."),
    }


def get_bot_status() -> dict:
    """Process + Activity-Status + Memory + Heartbeat-File.
    Phase-18: also surfaces classify_bot_processes() so callers can gate
    on multi-instance safety without re-scraping the process table."""
    procs = _collect_bot_processes()
    bot_alive = len(procs) > 0
    bot_memory_kb = sum(p.get("memory_kb", 0) for p in procs)
    bot_pid_count = len(procs)
    bot_proc_classification = classify_bot_processes(procs)
    log_size = LOG.stat().st_size if LOG.exists() else 0
    last_modified_sec_ago = (datetime.now().timestamp() - LOG.stat().st_mtime) if LOG.exists() else -1
    last_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:] if LOG.exists() else []
    # Heartbeat-File-Check
    hb_file = HERE / "heartbeat.txt"
    hb_age_sec = -1
    hb_content = ""
    if hb_file.exists():
        hb_age_sec = int(datetime.now().timestamp() - hb_file.stat().st_mtime)
        try:
            hb_content = hb_file.read_text(encoding="utf-8")[:50]
        except Exception:
            pass
    # Disk-space
    try:
        import shutil
        disk = shutil.disk_usage(str(HERE))
        disk_free_gb = disk.free / (1024**3)
        disk_used_pct = (disk.used / disk.total) * 100
    except Exception:
        disk_free_gb = -1
        disk_used_pct = -1
    return {
        "bot_process_alive": bot_alive,
        "bot_pid_count": bot_pid_count,  # Audit-Iter 29: explicit count
        "bot_proc_classification": bot_proc_classification,  # Phase-18 #5 P0
        "bot_memory_kb": bot_memory_kb,
        "bot_memory_mb": round(bot_memory_kb / 1024, 1),
        "log_file_size": log_size,
        "log_last_modified_sec_ago": int(last_modified_sec_ago),
        "log_last_3_lines": last_lines,
        "heartbeat_file_age_sec": hb_age_sec,
        "heartbeat_content": hb_content,
        "disk_free_gb": round(disk_free_gb, 1),
        "disk_used_pct": round(disk_used_pct, 1),
    }


def get_positions_count() -> int:
    """Open positions via Alpaca."""
    try:
        sys.path.insert(0, str(HERE))
        from secrets_loader import get_alpaca_keys
        # Phase-57: guarded audit check (ChatGPT P0 follow-up)
        try:
            from guarded_alpaca import GuardedTradingClient as _TC
        except Exception:
            from alpaca.trading.client import TradingClient as _TC
        k, s = get_alpaca_keys()
        client = _TC(k, s, paper=True)
        return len(client.get_all_positions())
    except Exception:
        return -1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--minutes", type=int, default=30, help="Look-back-window")
    args = p.parse_args()

    status = get_bot_status()
    lines = get_recent_log_lines(args.minutes)
    errors = classify_errors(lines)
    pos = get_positions_count()
    summary = {
        "timestamp": datetime.now().isoformat(),
        "bot_status": status,
        "positions_open": pos,
        "lines_in_window": len(lines),
        "errors_found": len(errors),
        "critical_errors": sum(1 for e in errors if e["severity"] == "critical"),
        "high_severity_errors": sum(1 for e in errors if e["severity"] == "high"),
        "auto_fixable_errors": sum(1 for e in errors if e["auto_fixable"]),
        "errors_by_category": {},
        "recent_errors": errors[-5:] if errors else [],
        "recommendation": "ok",
    }
    for e in errors:
        cat = e["category"]
        summary["errors_by_category"][cat] = summary["errors_by_category"].get(cat, 0) + 1

    # Recommendation
    # Phase-18 (ChatGPT-08:49 #5 P0): multi-independent-bot detection
    # takes precedence over every other recommendation — operator must
    # untangle the duplicate processes before anything else.
    proc_class = status.get("bot_proc_classification", {})
    if proc_class.get("classification") == "multiple_independent_bots":
        summary["recommendation"] = "BLOCK_MULTIPLE_INDEPENDENT_BOTS"
        summary["block_reason"] = proc_class.get("block_reason")
    elif not status["bot_process_alive"]:
        summary["recommendation"] = "RESTART_BOT_PROCESS_DEAD"
    elif summary["critical_errors"] > 0:
        summary["recommendation"] = "FIX_CRITICAL_THEN_RESTART"
    elif status["bot_memory_mb"] > 2000:  # 2 GB Memory-Limit
        summary["recommendation"] = "RESTART_MEMORY_HIGH"
    elif status["disk_free_gb"] >= 0 and status["disk_free_gb"] < 1.0:
        summary["recommendation"] = "ALERT_DISK_LOW"  # < 1 GB frei
    elif summary["high_severity_errors"] > 10:  # raised from 3 (Fix 12.05: yfinance-Spam-Toleranz)
        summary["recommendation"] = "INVESTIGATE_HIGH_SEVERITY"
    elif status["log_last_modified_sec_ago"] > 1200:
        summary["recommendation"] = "RESTART_LOG_STALE"
    elif status["heartbeat_file_age_sec"] >= 0 and status["heartbeat_file_age_sec"] > 1800:
        summary["recommendation"] = "RESTART_HEARTBEAT_STALE"  # >30min kein Heartbeat
    else:
        summary["recommendation"] = "ok"

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("=" * 60)
        print(f"AUDIT @ {summary['timestamp']}")
        print("=" * 60)
        print(f"  Bot-Process alive:  {status['bot_process_alive']} (Memory: {status['bot_memory_mb']} MB)")
        print(f"  Log last modified:  {status['log_last_modified_sec_ago']} sec ago")
        print(f"  Heartbeat-File:     {status['heartbeat_file_age_sec']} sec ago ('{status['heartbeat_content'][:30]}')")
        print(f"  Disk:               {status['disk_free_gb']} GB free ({status['disk_used_pct']}% used)")
        print(f"  Open positions:     {pos}")
        print(f"  Lines in last {args.minutes}min: {len(lines)}")
        print(f"  Errors found:      {len(errors)}")
        print(f"  ├ critical:        {summary['critical_errors']}")
        print(f"  ├ high:            {summary['high_severity_errors']}")
        print(f"  └ auto-fixable:    {summary['auto_fixable_errors']}")
        if summary["errors_by_category"]:
            print(f"  Error-Categories:")
            for cat, n in summary["errors_by_category"].items():
                print(f"    {cat}: {n}")
        if summary["recent_errors"]:
            print(f"  Recent (last 5):")
            for e in summary["recent_errors"]:
                print(f"    [{e['severity']}] {e['category']}: {e['line'][:120]}")
        print(f"  RECOMMENDATION: {summary['recommendation']}")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
