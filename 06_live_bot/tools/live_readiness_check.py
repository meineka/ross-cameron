"""Safe live-readiness validation for the paper trading bot.

This script performs read-only checks only: no order submission, no position
changes. It is intended as the operator gate before trusting the daemon for the
next live session.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
sys.path.insert(0, str(BOT_DIR))


def ny_today_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def check(name: str, fn):
    try:
        status, detail = fn()
    except Exception as exc:
        status = "FAIL"
        detail = f"{type(exc).__name__}: {exc}"
    return {"name": name, "status": status, "detail": detail}


def deps():
    missing = []
    for mod in ("alpaca", "yfinance", "pandas", "pyarrow", "psutil"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        return "FAIL", f"missing imports: {', '.join(missing)}"
    return "PASS", "required runtime imports available"


def secrets_present():
    from secrets_loader import get_alpaca_keys

    key, secret = get_alpaca_keys()
    if not key or not secret:
        return "FAIL", "Alpaca key/secret missing"
    return "PASS", "Alpaca key/secret loaded"


def alpaca_trading_readonly():
    from secrets_loader import get_alpaca_keys
    from alpaca.trading.client import TradingClient

    key, secret = get_alpaca_keys()
    client = TradingClient(key, secret, paper=True)
    account = client.get_account()
    clock = client.get_clock()
    positions = client.get_all_positions()
    if getattr(account, "account_blocked", False):
        return "FAIL", "paper account_blocked=True"
    if getattr(account, "trading_blocked", False):
        return "FAIL", "paper trading_blocked=True"
    detail = (
        f"account={account.status}, positions={len(positions)}, "
        f"clock_open={clock.is_open}, next_open={clock.next_open}"
    )
    return "PASS", detail


def alpaca_data_bars():
    from secrets_loader import get_alpaca_keys
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    key, secret = get_alpaca_keys()
    client = StockHistoricalDataClient(key, secret)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=10)
    bars = client.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=["SPY"],
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed="iex",
        )
    )
    count = len(getattr(bars, "data", {}).get("SPY", []))
    if count <= 0:
        return "FAIL", "Alpaca IEX daily bars for SPY returned empty"
    return "PASS", f"Alpaca IEX daily bars for SPY: {count}"


def yfinance_data():
    import yfinance as yf

    try:
        df = yf.Ticker("SPY").history(period="1d", interval="5m")
    except Exception as exc:
        return "WARN", f"yfinance unavailable/degraded: {type(exc).__name__}: {exc}"
    if df.empty:
        return "WARN", "yfinance returned empty SPY 5m data"
    return "PASS", f"yfinance SPY 5m rows: {len(df)}"


def process_audit():
    import audit

    status = audit.get_bot_status()
    lines = audit.get_recent_log_lines(120)
    errors = audit.classify_errors(lines)
    positions = audit.get_positions_count()
    critical = sum(1 for e in errors if e["severity"] == "critical")
    high = sum(1 for e in errors if e["severity"] == "high")
    if not status["bot_process_alive"]:
        return "FAIL", "bot daemon process is not alive"
    if critical or high > 10:
        return "FAIL", f"recent critical={critical}, high={high}"
    if status["heartbeat_file_age_sec"] > 1800:
        return "FAIL", f"heartbeat stale: {status['heartbeat_file_age_sec']}s"
    if status["log_last_modified_sec_ago"] > 1200:
        return "WARN", f"log stale: {status['log_last_modified_sec_ago']}s"
    return (
        "PASS",
        f"alive pid_count={status['bot_pid_count']}, heartbeat={status['heartbeat_file_age_sec']}s, "
        f"positions={positions}, recent_errors={len(errors)}",
    )


def local_files():
    details = []
    failures = []
    warnings = []
    for name in ("heartbeat.txt", "daemon.log", "bot.log", ".env"):
        path = BOT_DIR / name
        if not path.exists():
            failures.append(f"{name} missing")
            continue
        age = int(datetime.now().timestamp() - path.stat().st_mtime)
        details.append(f"{name} age={age}s size={path.stat().st_size}B")
    watchlist = BOT_DIR / "watchlist_today.json"
    if watchlist.exists():
        try:
            payload = json.loads(watchlist.read_text(encoding="utf-8"))
            watchlist_date = payload.get("date")
            details.append(f"watchlist_date={watchlist_date} symbols={len(payload.get('symbols', []))}")
            if watchlist_date != ny_today_str():
                warnings.append(
                    f"watchlist_today.json is stale ({watchlist_date}); expected before today's first scan, "
                    "but it must not be used for live decisions"
                )
        except Exception as exc:
            warnings.append(f"watchlist unreadable: {exc}")
    else:
        warnings.append("watchlist_today.json missing until first session scan")
    status_json = BOT_DIR / "status.json"
    if status_json.exists():
        age = int(datetime.now().timestamp() - status_json.stat().st_mtime)
        if age > 3600:
            warnings.append(
                f"status.json is stale ({age}s); heartbeat/log audit is the source of truth while daemon sleeps"
            )
    if failures:
        return "FAIL", "; ".join(failures + details)
    if warnings:
        return "WARN", "; ".join(warnings + details)
    return "PASS", "; ".join(details)


def main():
    checks = [
        check("runtime_dependencies", deps),
        check("secrets_present", secrets_present),
        check("alpaca_trading_readonly", alpaca_trading_readonly),
        check("alpaca_data_bars", alpaca_data_bars),
        check("yfinance_data", yfinance_data),
        check("process_audit", process_audit),
        check("local_files", local_files),
    ]
    failed = [c for c in checks if c["status"] == "FAIL"]
    warned = [c for c in checks if c["status"] == "WARN"]
    overall = "FAIL" if failed else ("WARN" if warned else "PASS")
    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "overall": overall,
        "checks": checks,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
