"""deploy_safe.py — Deploy + Restart nur wenn KEINE offenen Positions.

Exit-Codes:
  0 = Restart erfolgreich
  1 = Positions offen → kein Restart
  2 = Alpaca-Connection-Fehler
  3 = Restart fehlgeschlagen
"""
from __future__ import annotations
import sys, io, os, subprocess, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path

HERE = Path(__file__).resolve().parent

def check_positions() -> tuple[bool, int]:
    """Returns (positions_open, count). True wenn aktive Positions."""
    try:
        from alpaca.trading.client import TradingClient
        api_key = os.environ.get("APCA_API_KEY_ID", "")
        api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        if not api_key:
            print("[FAIL] env-vars fehlen")
            return False, -1
        client = TradingClient(api_key, api_secret, paper=True)
        positions = client.get_all_positions()
        return len(positions) > 0, len(positions)
    except Exception as e:
        print(f"[FAIL] Alpaca-check: {e}")
        return False, -1


def kill_bot():
    """Kill alle python.exe."""
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"],
                   capture_output=True, text=True)
    time.sleep(2)


def start_bot() -> int:
    """Startet bot.py --daemon detached, returns PID oder 0 bei Fehler."""
    env = os.environ.copy()
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
    print("Killing old bot…")
    kill_bot()
    pid = start_bot()
    if pid:
        print(f"OK: Bot restarted, new PID {pid}")
        sys.exit(0)
    else:
        print("FAIL: Bot-Start error")
        sys.exit(3)


if __name__ == "__main__":
    main()
