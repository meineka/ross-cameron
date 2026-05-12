"""watchdog.py — Cameron-Bot Watchdog.

Checkt alle 5 Min ob bot.py noch läuft. Wenn nicht: restart.
Schreibt watchdog.log mit allen Aktionen.

Start:
  cd 06_live_bot
  start /B python watchdog.py > watchdog.log 2>&1
"""
from __future__ import annotations
import os, sys, io, time, subprocess, logging
from pathlib import Path
from datetime import datetime, timezone

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_log_path = Path(__file__).parent / "watchdog.log"
_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _handlers.append(logging.FileHandler(_log_path, encoding="utf-8", mode="a"))
except (PermissionError, OSError):
    # Fallback if log file locked (race condition with stdout redirect)
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=_handlers)
log = logging.getLogger("watchdog")

CHECK_INTERVAL_SEC = 300  # 5 Min
HERE = Path(__file__).resolve().parent


def is_bot_running() -> tuple[bool, list[int]]:
    """True if any python.exe process is running bot.py --daemon."""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            text=True, timeout=10,
        )
        pids: list[int] = []
        for line in out.splitlines():
            if "bot.py" in line and "--daemon" in line:
                # CSV: Node,CommandLine,ProcessId
                parts = line.rsplit(",", 1)
                if len(parts) == 2 and parts[1].strip().isdigit():
                    pids.append(int(parts[1].strip()))
        return len(pids) > 0, pids
    except Exception as e:
        log.warning("is_bot_running check failed: %s", e)
        return False, []


def start_bot():
    """Start bot.py --daemon as detached background process.

    Audit-Bug-Fix 2026-05-12 (Iter 4):
    - Bug T: Hardcoded API keys waren im source committed — entfernt,
      jetzt via secrets_loader (.env oder env-vars)
    - Bug U: Trade-Lock check vor Restart — wenn offene Positions,
      kein blind restart (Position-Recovery würde sie flatten)
    """
    # Trade-Lock via secrets_loader + Alpaca-Check (Bug U)
    sys.path.insert(0, str(HERE))
    try:
        from secrets_loader import get_alpaca_keys
        key, sec = get_alpaca_keys()
    except Exception as e:
        log.error("Watchdog: secrets unavailable — abort restart: %s", e)
        return None
    try:
        from alpaca.trading.client import TradingClient
        tc = TradingClient(key, sec, paper=True)
        positions = tc.get_all_positions()
        if positions:
            log.warning("BLOCKED restart: %d positions open — let them resolve naturally", len(positions))
            for p in positions:
                log.warning("  %s qty=%s avg=%s", p.symbol, p.qty, p.avg_entry_price)
            return None
    except Exception as e:
        log.error("Watchdog: position-check failed — abort restart: %s", e)
        return None

    env = os.environ.copy()
    env["APCA_API_KEY_ID"] = key
    env["APCA_API_SECRET_KEY"] = sec
    log_path = HERE / "daemon.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, "bot.py", "--daemon"],
            cwd=str(HERE), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    log.info("Started bot.py --daemon → PID %d", proc.pid)
    return proc.pid


def main():
    log.info("=" * 60)
    log.info("WATCHDOG START — checks every %d sec", CHECK_INTERVAL_SEC)
    log.info("=" * 60)
    while True:
        running, pids = is_bot_running()
        if running:
            log.info("Bot OK — PIDs %s", pids)
        else:
            log.warning("Bot NOT running → restarting…")
            try:
                start_bot()
            except Exception as e:
                log.error("Restart failed: %s", e)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
