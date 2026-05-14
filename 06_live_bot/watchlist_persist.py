"""Watchlist-Persistenz für Crash-Recovery.

2026-05-11-Lesson: nach Crash war 12:27-Watchlist nur im RAM → Tag verloren.
Jetzt: nach jedem successful Premarket-Scan auf Disk schreiben mit Datum.
Bei Restart innerhalb des Trading-Fensters laden statt neu zu scannen.

Audit-Iter 30 (2026-05-13) — Bug-Fixes WP-1/WP-5/WP-6:
  WP-1: atomic write (tmp + os.replace) — crash mid-write hätte sonst
        corrupt JSON gelassen.
  WP-5: load_watchlist_with_scores zusätzlich exportiert (Loader gibt
        scores zurück, nicht nur symbols).
  WP-6 (kritisch): load_watchlist_if_fresh wurde IMPORTIERT aber NIE
        AUFGERUFEN — die ganze Mid-Day-Resume-Feature war broken seit
        Existenz. Fix wird in bot.py daemon_run gewired.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger("watchlist_persist")

WATCHLIST_FILE = Path(__file__).parent / "watchlist_today.json"


def _ny_today_str() -> str:
    """Review-V2 P2.6: trading-day decisions MUST use NY timezone, not
    server-local. If the bot runs on a Berlin-hosted server, midnight CET
    is 18:00 NY-yesterday — saving with local-now() would assign yesterday's
    watchlist to today's date.
    """
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def save_watchlist(symbols: list[str], scores: dict[str, float] | None = None) -> None:
    """Atomic write via tmp + rename."""
    payload = {
        "date": _ny_today_str(),  # P2.6: NY trading-day, not local
        "saved_at": datetime.now().isoformat(),
        "symbols": symbols,
        "scores": scores or {},
    }
    serialized = json.dumps(payload, indent=2)
    tmp = WATCHLIST_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        os.replace(str(tmp), str(WATCHLIST_FILE))
    except OSError as e:
        log.warning("save_watchlist failed: %s", e)
        try: tmp.unlink(missing_ok=True)
        except Exception: pass


def _load_payload_if_fresh() -> dict | None:
    """Returns full payload dict if date matches today, else None.
    Defensive against corrupt JSON, missing keys, etc."""
    if not WATCHLIST_FILE.exists():
        return None
    try:
        payload = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            log.warning("watchlist file unexpected format (not dict)")
            return None
        if payload.get("date") != _ny_today_str():  # P2.6: NY trading-day
            return None
        return payload
    except json.JSONDecodeError as e:
        log.warning("watchlist file corrupt JSON: %s", e)
        return None
    except OSError as e:
        log.warning("watchlist file read err: %s", e)
        return None


def load_watchlist_if_fresh() -> list[str] | None:
    """Liefert die Symbole wenn die Datei von HEUTE ist, sonst None."""
    payload = _load_payload_if_fresh()
    if payload is None:
        return None
    syms = payload.get("symbols")
    return syms if isinstance(syms, list) else None


def load_watchlist_with_scores() -> tuple[list[str], dict[str, float]] | None:
    """Audit-Iter 30: Loader-API mit scores für mid-day-resume.
    Returns (symbols, scores) tuple oder None."""
    payload = _load_payload_if_fresh()
    if payload is None:
        return None
    syms = payload.get("symbols")
    scores = payload.get("scores", {})
    if not isinstance(syms, list):
        return None
    # scores might be missing or non-dict — defensive
    if not isinstance(scores, dict):
        scores = {}
    return syms, scores


def clear_watchlist() -> None:
    try:
        if WATCHLIST_FILE.exists():
            WATCHLIST_FILE.unlink()
    except OSError as e:
        log.warning("clear_watchlist failed: %s", e)
