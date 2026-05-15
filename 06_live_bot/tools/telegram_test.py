"""telegram_test.py — Phase-25 helper: smoke-test the Telegram alerter.

Run BEFORE you trust the live monitor: this sends a single test message
to confirm your TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID work.

  python 06_live_bot/tools/telegram_test.py

Expected output:
  alerter selected: TelegramAlerter (or CompositeAlerter [Telegram + Log])
  sending test message...
  delivered: True

If "delivered: False":
  - check token (no spaces, no extra chars)
  - check chat_id (your numeric ID, not username)
  - check you've /started the bot in Telegram once
  - watch alerts.log for the error reason
"""
from __future__ import annotations
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from alerter import make_alerter, TelegramAlerter, CompositeAlerter, LogAlerter


def main():
    a = make_alerter()
    print(f"alerter selected: {type(a).__name__}")
    if isinstance(a, CompositeAlerter):
        print("  children:")
        for c in a.alerters:
            print(f"    - {type(c).__name__}")

    has_telegram = (
        isinstance(a, TelegramAlerter)
        or (isinstance(a, CompositeAlerter)
            and any(isinstance(c, TelegramAlerter) for c in a.alerters))
    )
    if not has_telegram:
        print()
        print("[!] No TelegramAlerter wired. Check .env contains:")
        print("    TELEGRAM_BOT_TOKEN=...")
        print("    TELEGRAM_CHAT_ID=...")
        print(f"Currently using {type(a).__name__} - alerts go to disk only.")
        return 1

    print("sending test message...")
    ok = a.send(
        level="info",
        title="Cameron-Bot Telegram Test",
        body=("Wenn du das hier siehst läuft die Alerter-Pipeline.\n\n"
              "Push-Notifications werden ab jetzt bei Folgendem feuern:\n"
              "  • Bot-Prozess tot oder doppelt\n"
              "  • Heartbeat > 30 min stale\n"
              "  • Alpaca account inaktiv / data feed down\n"
              "  • yfinance unreachable\n"
              "  • Catalyst-News-API broken\n\n"
              "2 consecutive failures → 1 alert. Recovery → recovered-info.\n"
              "Debounce 15 min pro (level, title) gegen Spam."),
        force=True,
    )
    print(f"delivered: {ok}")
    if not ok:
        print()
        print("Lies tail von alerts.log für den Fehlergrund:")
        print(f"  Get-Content {HERE / 'alerts.log'} -Tail 5")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
