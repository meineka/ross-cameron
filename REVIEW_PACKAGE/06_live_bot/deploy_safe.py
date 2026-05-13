"""deploy_safe.py — Deploy + Restart nur wenn KEINE offenen Positions.

Exit-Codes:
  0 = Restart erfolgreich
  1 = Positions offen → kein Restart
  2 = Alpaca-Connection-Fehler
  3 = Restart fehlgeschlagen

Audit-Iter 34 (2026-05-13) — Bug-Fixes DS-1/DS-2/DS-5/DS-6:
  DS-1: taskkill killed JEDES python.exe (auch jupyter, tests, ...).
        Jetzt: cmdline-Match auf 'bot.py' + '--daemon' wie watchdog.py.
  DS-2: Race-Window zwischen check + kill+restart → re-check positions
        nach kill, vor start (bot könnte zwischen den Calls noch tradet).
  DS-5: /F war SIGKILL ohne graceful shutdown — Bot konnte nicht HARD_FLAT
        + day-summary schreiben. Jetzt: SIGTERM first (15s grace), dann
        SIGKILL.
  DS-6: cross-platform (psutil fallback statt nur Windows).
"""
from __future__ import annotations
import sys, io, os, subprocess, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def check_positions() -> tuple[bool, int]:
    """Returns (positions_open, count). True wenn aktive Positions.
    n=-1 = unknown (API fail) — caller darf NICHT als safe interpretieren."""
    try:
        from alpaca.trading.client import TradingClient
        from secrets_loader import get_alpaca_keys
        try:
            api_key, api_secret = get_alpaca_keys()
        except Exception:
            api_key = os.environ.get("APCA_API_KEY_ID", "")
            api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not (api_key and api_secret):
            print("[FAIL] keys fehlen (env vars + .env)")
            return False, -1
        client = TradingClient(api_key, api_secret, paper=True)
        positions = client.get_all_positions()
        return len(positions) > 0, len(positions)
    except Exception as e:
        print(f"[FAIL] Alpaca-check: {e}")
        return False, -1


def _find_bot_pids() -> list[int]:
    """Audit-Iter 34 (DS-1/DS-6): cross-platform + cmdline-specific match
    auf bot.py --daemon. Niemals andere python-Prozesse erwischen."""
    pids = []
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "bot.py" in cmdline and "--daemon" in cmdline:
                    pids.append(p.pid)
            except Exception:
                pass
        return pids
    except ImportError:
        pass
    # Fallback OS-specific
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "CommandLine,ProcessId", "/format:csv"],
                text=True, timeout=10,
            )
            for line in out.splitlines():
                if "bot.py" in line and "--daemon" in line:
                    parts = line.rsplit(",", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        pids.append(int(parts[1].strip()))
        except Exception:
            pass
    else:
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "bot.py.*--daemon"],
                text=True, timeout=5,
            )
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
        except Exception:
            pass
    return pids


def kill_bot(graceful_seconds: float = 15.0) -> int:
    """Kill bot.py --daemon. Returns Anzahl gekilleter PIDs.

    SIGTERM first (graceful → Bot kann HARD_FLAT + day_summary schreiben),
    SIGKILL fallback nach graceful_seconds.
    """
    pids = _find_bot_pids()
    if not pids:
        return 0
    print(f"  Found {len(pids)} bot PID(s): {pids}")
    # 1. graceful SIGTERM
    try:
        import psutil, signal
        for pid in pids:
            try:
                p = psutil.Process(pid)
                if os.name == "nt":
                    p.terminate()
                else:
                    p.send_signal(signal.SIGTERM)
            except Exception:
                pass
    except ImportError:
        # Fallback
        for pid in pids:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid)],
                                   capture_output=True, text=True)
                else:
                    os.kill(pid, 15)  # SIGTERM
            except Exception:
                pass
    # 2. wait for graceful exit
    deadline = time.time() + graceful_seconds
    while time.time() < deadline:
        remaining = _find_bot_pids()
        if not any(p in pids for p in remaining):
            return len(pids)  # all dead
        time.sleep(0.5)
    # 3. SIGKILL fallback
    print(f"  Graceful timeout — force-killing")
    remaining = [p for p in _find_bot_pids() if p in pids]
    for pid in remaining:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, text=True)
            else:
                os.kill(pid, 9)  # SIGKILL
        except Exception:
            pass
    time.sleep(1)
    return len(pids)


def start_bot() -> int:
    """Startet bot.py --daemon detached, returns PID oder 0 bei Fehler."""
    env = os.environ.copy()
    try:
        from secrets_loader import get_alpaca_keys
        k, s = get_alpaca_keys()
        env.setdefault("APCA_API_KEY_ID", k)
        env.setdefault("APCA_API_SECRET_KEY", s)
    except Exception as e:
        print(f"[FAIL] secrets_loader: {e}")
        sys.exit(2)
    log_path = HERE / "daemon.log"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with open(log_path, "ab") as logf:
        proc = subprocess.Popen(
            [sys.executable, "bot.py", "--daemon"],
            cwd=str(HERE), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    time.sleep(3)
    return proc.pid


def main():
    print(f"=== DEPLOY-SAFE @ {time.strftime('%H:%M:%S')} ===")
    open_, n = check_positions()
    if n == -1:
        print("FAIL: Cannot check positions — abort restart")
        sys.exit(2)
    if open_:
        print(f"BLOCKED: {n} Positions offen → kein Restart, retry später")
        sys.exit(1)
    print("OK: keine offenen Positions")
    print("Killing old bot (graceful)...")
    n_killed = kill_bot()
    print(f"  Killed {n_killed} bot process(es)")
    # Audit-Iter 34 (DS-2): re-check positions AFTER kill, before start.
    # Bot kann zwischen erstem check und kill noch eine Position eröffnet
    # haben → safety re-check.
    open2, n2 = check_positions()
    if n2 > 0:
        print(f"WARNING: {n2} positions opened during shutdown! Bot will recover them.")
    pid = start_bot()
    if pid:
        print(f"OK: Bot restarted, new PID {pid}")
        sys.exit(0)
    else:
        print("FAIL: Bot-Start error")
        sys.exit(3)


if __name__ == "__main__":
    # Audit-Iter 34: stdout-wrapper nur als script, nicht beim import
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
