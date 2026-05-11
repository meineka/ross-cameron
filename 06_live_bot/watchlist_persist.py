"""Watchlist-Persistenz für Crash-Recovery.

2026-05-11-Lesson: nach Crash war 12:27-Watchlist nur im RAM → Tag verloren.
Jetzt: nach jedem successful Premarket-Scan auf Disk schreiben mit Datum.
Bei Restart innerhalb des Trading-Fensters laden statt neu zu scannen.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

WATCHLIST_FILE = Path(__file__).parent / "watchlist_today.json"


def save_watchlist(symbols: list[str], scores: dict[str, float] | None = None) -> None:
    payload = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "saved_at": datetime.now().isoformat(),
        "symbols": symbols,
        "scores": scores or {},
    }
    WATCHLIST_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_watchlist_if_fresh() -> list[str] | None:
    """Liefert die Symbole, wenn die Datei von HEUTE ist. Sonst None."""
    if not WATCHLIST_FILE.exists():
        return None
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        if payload.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return payload["symbols"]
    except Exception:
        return None
    return None


def clear_watchlist() -> None:
    if WATCHLIST_FILE.exists():
        WATCHLIST_FILE.unlink()
