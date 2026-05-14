"""watchdog.py — Cameron-Bot Watchdog.

Checkt alle 5 Min ob bot.py noch läuft. Wenn nicht: restart.
Schreibt watchdog.log mit allen Aktionen.

Start:
  cd 06_live_bot
  start /B python watchdog.py > watchdog.log 2>&1

Phase-12 (ChatGPT-19:05 P0.1): Watchdog resolves a BOT_PYTHON
interpreter explicitly (env var → .venv → sys.executable), runs a
dependency preflight against it, and never spam-restarts when the
runtime lacks alpaca/yfinance/pandas/pyarrow.
"""
from __future__ import annotations
import os, sys, io, time, subprocess, logging
from collections import deque
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
# Audit-Iter 14: Crashloop-Protection
MAX_RESTARTS_PER_HOUR = 5
RESTART_WINDOW_SEC = 3600
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Phase-12: dependencies the bot needs at runtime. Watchdog will refuse
# to restart-spam if any of these can't be imported by the resolved
# bot-Python.
REQUIRED_DEPS = ("alpaca", "yfinance", "pandas", "pyarrow")


class CheckUnknown(Exception):
    """is_bot_running konnte den State nicht ermitteln (wmic timeout etc).
    Audit-Iter 14: caller MUSS unterscheiden zwischen 'tot' und 'unklar',
    sonst false-positive restart bei wmic-Hänger."""


class DependencyError(Exception):
    """Phase-12 (ChatGPT-19:05 P0.1): resolved bot-Python lacks required
    runtime deps. Surfaces operator-action instead of restart-spam."""


def resolve_bot_python() -> str:
    """Phase-12: resolve which Python the bot should run under.

    Priority:
      1. Environment variable BOT_PYTHON (operator override).
      2. Local .venv\\Scripts\\python.exe under repo-root or 06_live_bot.
      3. Local .venv/bin/python on POSIX.
      4. sys.executable (the interpreter running the watchdog) — used
         only after preflight confirms deps.
    Returns the absolute path string.
    """
    env_py = os.environ.get("BOT_PYTHON")
    if env_py and Path(env_py).exists():
        return env_py
    candidates = []
    if os.name == "nt":
        candidates.append(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
        candidates.append(HERE / ".venv" / "Scripts" / "python.exe")
    else:
        candidates.append(REPO_ROOT / ".venv" / "bin" / "python")
        candidates.append(HERE / ".venv" / "bin" / "python")
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


def preflight_dependencies(python_path: str, deps: tuple = REQUIRED_DEPS) -> tuple[bool, list[str]]:
    """Phase-12: probe the resolved bot-Python for required deps.

    Runs `<python_path> -c "import alpaca; import yfinance; ..."` in a
    subprocess and returns (ok, missing_deps). The subprocess is short
    (import-only) and cannot spam — called ONCE at watchdog start and
    every N cycles after dep-error.
    """
    code = "; ".join(f"import {d}" for d in deps)
    try:
        proc = subprocess.run(
            [python_path, "-c", code],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("preflight: subprocess failed: %s", e)
        return False, list(deps)
    if proc.returncode == 0:
        return True, []
    missing = []
    err = (proc.stderr or "") + (proc.stdout or "")
    for d in deps:
        if f"No module named '{d}'" in err or f"No module named \"{d}\"" in err:
            missing.append(d)
    if not missing:
        # Generic import failure — assume all suspect, surface raw stderr
        log.error("preflight: import probe failed with: %s", err.strip()[:500])
        missing = list(deps)
    return False, missing


def is_bot_running() -> tuple[bool, list[int]]:
    """True if any python.exe process is running bot.py --daemon.

    Audit-Iter 14 (Bug-Fix WD-3): bei check-failure jetzt CheckUnknown
    statt False zurück. Vorher: wmic hängt → return False → unnötiger
    Restart neben noch lebendem Bot → 2 Bots parallel.
    """
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            text=True, timeout=10,
        )
    except Exception as e:
        log.warning("is_bot_running check failed: %s", e)
        raise CheckUnknown(str(e))
    pids: list[int] = []
    for line in out.splitlines():
        if "bot.py" in line and "--daemon" in line:
            # CSV: Node,CommandLine,ProcessId
            parts = line.rsplit(",", 1)
            if len(parts) == 2 and parts[1].strip().isdigit():
                pids.append(int(parts[1].strip()))
    return len(pids) > 0, pids


def _position_check_via_bot_python(bot_python: str, key: str, sec: str) -> tuple[bool, int]:
    """Phase-12: run the open-position-check in a subprocess that uses
    BOT_PYTHON. This stops the watchdog Python from needing alpaca
    importable directly. Returns (ok, position_count).

    ok=False means "could not determine" — caller must NOT restart in
    that case (mirror of CheckUnknown semantics for the position-check)."""
    code = (
        "import os, json, sys\n"
        "from alpaca.trading.client import TradingClient\n"
        "tc = TradingClient(os.environ['APCA_API_KEY_ID'], os.environ['APCA_API_SECRET_KEY'], paper=True)\n"
        "ps = tc.get_all_positions()\n"
        "print(json.dumps({'n': len(ps), 'symbols': [p.symbol for p in ps]}))\n"
    )
    env = os.environ.copy()
    env["APCA_API_KEY_ID"] = key
    env["APCA_API_SECRET_KEY"] = sec
    try:
        proc = subprocess.run(
            [bot_python, "-c", code],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("position-check subprocess failed: %s", e)
        return False, -1
    if proc.returncode != 0:
        log.error("position-check rc=%d stderr=%s", proc.returncode, (proc.stderr or "").strip()[:500])
        return False, -1
    import json as _json
    try:
        info = _json.loads(proc.stdout.strip().splitlines()[-1])
        return True, int(info.get("n", 0))
    except Exception as e:
        log.error("position-check: bad output: %s — raw=%s", e, proc.stdout[:200])
        return False, -1


def start_bot(bot_python: str | None = None):
    """Start bot.py --daemon as detached background process.

    Audit-Bug-Fix 2026-05-12 (Iter 4):
    - Bug T: Hardcoded API keys waren im source committed — entfernt,
      jetzt via secrets_loader (.env oder env-vars)
    - Bug U: Trade-Lock check vor Restart — wenn offene Positions,
      kein blind restart (Position-Recovery würde sie flatten)
    Phase-12 (ChatGPT-19:05 P0.1): bot_python is resolved explicitly
    via resolve_bot_python(); position-check runs in that interpreter
    rather than the watchdog's own Python.
    """
    if bot_python is None:
        bot_python = resolve_bot_python()

    # Trade-Lock via secrets_loader (still needs to run in *some* Python;
    # secrets_loader is pure-stdlib so the watchdog's own Python is fine)
    sys.path.insert(0, str(HERE))
    try:
        from secrets_loader import get_alpaca_keys
        key, sec = get_alpaca_keys()
    except Exception as e:
        log.error("Watchdog: secrets unavailable — abort restart: %s", e)
        return None

    ok, n_pos = _position_check_via_bot_python(bot_python, key, sec)
    if not ok:
        log.error("Watchdog: position-check failed — abort restart (no double-launch)")
        return None
    if n_pos > 0:
        log.warning("BLOCKED restart: %d positions open — let them resolve naturally", n_pos)
        return None

    env = os.environ.copy()
    env["APCA_API_KEY_ID"] = key
    env["APCA_API_SECRET_KEY"] = sec
    log_path = HERE / "daemon.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(
            [bot_python, "bot.py", "--daemon"],
            cwd=str(HERE), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    log.info("Started bot.py --daemon → PID %d (python=%s)", proc.pid, bot_python)
    return proc.pid


def _restart_loop_should_abort(restart_times: deque[float],
                                 now: float | None = None) -> bool:
    """Audit-Iter 14 (Bug-Fix WD-1): wenn > MAX_RESTARTS_PER_HOUR in den
    letzten RESTART_WINDOW_SEC → Crashloop, kein weiterer Restart."""
    now = now if now is not None else time.time()
    cutoff = now - RESTART_WINDOW_SEC
    while restart_times and restart_times[0] < cutoff:
        restart_times.popleft()
    return len(restart_times) >= MAX_RESTARTS_PER_HOUR


def main():
    log.info("=" * 60)
    log.info("WATCHDOG START — checks every %d sec (max %d restarts/h)",
             CHECK_INTERVAL_SEC, MAX_RESTARTS_PER_HOUR)

    # Phase-12: resolve bot-Python + dependency-preflight ONCE up-front.
    # If deps are missing we exit with a clear operator-action message
    # rather than burning a restart-budget on doomed launches.
    bot_python = resolve_bot_python()
    log.info("Bot-Python resolved → %s", bot_python)
    ok, missing = preflight_dependencies(bot_python)
    if not ok:
        log.error("=" * 60)
        log.error("DEPENDENCY PREFLIGHT FAILED")
        log.error("Missing modules in %s: %s", bot_python, missing)
        log.error("Operator action:")
        log.error("  Option A: set BOT_PYTHON=<path-to-python-with-deps>")
        log.error("  Option B: %s -m pip install %s",
                  bot_python, " ".join(missing))
        log.error("  Option C: create .venv at repo-root or 06_live_bot and install requirements.txt")
        log.error("Watchdog exits — restart manually after fix.")
        log.error("=" * 60)
        return
    log.info("Preflight OK — deps importable: %s", REQUIRED_DEPS)
    log.info("=" * 60)

    restart_times: deque[float] = deque()
    while True:
        try:
            running, pids = is_bot_running()
        except CheckUnknown as e:
            # Audit-Iter 14: bei unklarem State NICHT restarten — könnte
            # ein zweiter Bot daneben gestartet werden
            log.warning("Skip cycle (state unknown): %s", e)
            time.sleep(CHECK_INTERVAL_SEC)
            continue
        if running:
            log.info("Bot OK — PIDs %s", pids)
        else:
            if _restart_loop_should_abort(restart_times):
                log.error("=" * 60)
                log.error("CRASHLOOP DETECTED: %d restarts in last %d sec — STOP",
                          len(restart_times), RESTART_WINDOW_SEC)
                log.error("Manual investigation needed. Watchdog exits.")
                log.error("=" * 60)
                return
            log.warning("Bot NOT running → restarting…")
            try:
                pid = start_bot(bot_python)
                if pid is not None:
                    restart_times.append(time.time())
            except Exception as e:
                log.error("Restart failed: %s", e)
                restart_times.append(time.time())
        time.sleep(CHECK_INTERVAL_SEC)


def preflight_only() -> int:
    """Phase-13 (ChatGPT-20:11): run resolve + dep-preflight, print result,
    exit. No restart, no monitoring loop. Operator-CLI for verifying the
    Python/dep setup before kicking off a real watchdog."""
    bot_python = resolve_bot_python()
    print(f"Bot-Python resolved -> {bot_python}")
    ok, missing = preflight_dependencies(bot_python)
    if ok:
        print(f"Preflight OK -> deps importable: {REQUIRED_DEPS}")
        return 0
    print("DEPENDENCY PREFLIGHT FAILED")
    print(f"Missing modules in {bot_python}: {missing}")
    print("Operator action: set BOT_PYTHON, or pip install missing deps, or "
          "create .venv at repo-root.")
    return 1


if __name__ == "__main__":
    if "--preflight-only" in sys.argv:
        sys.exit(preflight_only())
    main()
