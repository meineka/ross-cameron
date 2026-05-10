"""audit.py — Health-Audit von daemon.log, klassifiziert Errors.

Nutzung von Auto-Healer:
  python audit.py           # zeigt aktuelle Status + Error-Klassifizierung
  python audit.py --json    # maschinen-lesbar
"""
from __future__ import annotations
import sys, io, json, re, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
from datetime import datetime, timedelta
import subprocess

HERE = Path(__file__).resolve().parent
LOG = HERE / "daemon.log"

# Error-Categories mit Auto-Fix-Hinweis
ERROR_PATTERNS = [
    # (regex, kategorie, severity, auto_fixable, fix_hint)
    (r"YFRateLimitError|429|Too Many Requests", "yfinance_rate_limit", "low", True, "wartet selbst"),
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
    """Lese Zeilen aus log die letzten N Minuten."""
    if not LOG.exists():
        return []
    cutoff = datetime.now() - timedelta(minutes=minutes)
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m:
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    out.append(line)
            except Exception:
                pass
    return out


def classify_errors(lines: list[str]) -> list[dict]:
    """Klassifiziere Errors in den Lines."""
    findings = []
    for line in lines:
        if "ERROR" not in line and "Traceback" not in line and "FAIL" not in line:
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
            findings.append({
                "line": line,
                "category": "unknown",
                "severity": "high",
                "auto_fixable": False,
                "fix_hint": "needs human review",
            })
    return findings


def get_bot_status() -> dict:
    """Process + Activity-Status."""
    try:
        out = subprocess.check_output(["tasklist"], text=True, timeout=5)
        bot_alive = sum(1 for l in out.splitlines() if "python.exe" in l) > 0
    except Exception:
        bot_alive = False
    log_size = LOG.stat().st_size if LOG.exists() else 0
    last_modified_sec_ago = (datetime.now().timestamp() - LOG.stat().st_mtime) if LOG.exists() else -1
    last_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-3:] if LOG.exists() else []
    return {
        "bot_process_alive": bot_alive,
        "log_file_size": log_size,
        "log_last_modified_sec_ago": int(last_modified_sec_ago),
        "log_last_3_lines": last_lines,
    }


def get_positions_count() -> int:
    """Open positions via Alpaca."""
    try:
        import os
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            os.environ.get("APCA_API_KEY_ID", ""),
            os.environ.get("APCA_API_SECRET_KEY", ""),
            paper=True,
        )
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
    if not status["bot_process_alive"]:
        summary["recommendation"] = "RESTART_BOT_PROCESS_DEAD"
    elif summary["critical_errors"] > 0:
        summary["recommendation"] = "FIX_CRITICAL_THEN_RESTART"
    elif summary["high_severity_errors"] > 3:
        summary["recommendation"] = "INVESTIGATE_HIGH_SEVERITY"
    elif status["log_last_modified_sec_ago"] > 1200:
        summary["recommendation"] = "RESTART_LOG_STALE"  # >20 min keine Aktivität
    else:
        summary["recommendation"] = "ok"

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print("=" * 60)
        print(f"AUDIT @ {summary['timestamp']}")
        print("=" * 60)
        print(f"  Bot-Process alive: {status['bot_process_alive']}")
        print(f"  Log last modified: {status['log_last_modified_sec_ago']} sec ago")
        print(f"  Open positions:    {pos}")
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
    main()
