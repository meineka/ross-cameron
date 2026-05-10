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
    """Start bot.py --daemon as detached background process."""
    env = os.environ.copy()
    # Falls Env-Vars nicht im Watchdog-Process: hardcoded fallback (NUR Paper)
    env.setdefault("APCA_API_KEY_ID", "PKBERNOMU23XEGRU5SPD3JZGDX")
    env.setdefault("APCA_API_SECRET_KEY", "FZBBx9v8Pw7eaLRFD8wW51WNnVkWeWNkts2D7zRSaxaB")
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
