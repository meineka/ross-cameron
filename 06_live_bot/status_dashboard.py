"""Schreibt jede Iteration status.json — operator sieht Live-State ohne Log-Grep.

Felder: timestamp, account_equity, positions, watchlist, day-stats, last_health.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

STATUS_FILE = Path(__file__).parent / "status.json"


def write_status(bot) -> None:
    """Wird von Bot.run() periodisch aufgerufen. Best-effort — Fehler still."""
    try:
        d = bot.day
        positions = []
        for ts in bot.tickers.values():
            if getattr(ts, "in_position", False):
                positions.append({
                    "symbol": ts.symbol,
                    "shares": getattr(ts, "shares", 0),
                    "entry": getattr(ts, "entry_price", 0.0),
                })
        watchlist = [
            {"symbol": s, "rank": getattr(t, "rank", -1), "score": getattr(t, "score", 0.0)}
            for s, t in bot.tickers.items()
        ]
        payload = {
            "ts": datetime.now().isoformat(),
            "account_equity": getattr(bot, "_last_equity", None),
            "realized_pnl": round(d.realized_pnl, 2),
            "peak_pnl": round(d.peak_pnl, 2),
            "spy_pct": round(d.spy_pct_today, 3),
            "trades_today": getattr(d, "trades_today", 0),
            "consecutive_losses": d.consecutive_losses,
            "spiral_locked": d.spiral_locked,
            "ws_reconnects": d.ws_reconnects,
            "positions_open": positions,
            "watchlist": watchlist,
        }
        STATUS_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass
