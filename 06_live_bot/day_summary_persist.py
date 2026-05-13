"""End-of-Day Summary → results/YYYY-MM-DD.json für historische Trends.

Audit-Iter 28 (2026-05-13) — Bug-Fixes DSP-1/DSP-2/DSP-5:
  DSP-1: atomic write (tmp + os.replace) — Crash mid-write hätte sonst
         day-summary halb-geschrieben gelassen.
  DSP-2: Trading-Day-Date statt System-Local. Bot in UTC-Cloud lief gestern
         um 22:00 UTC = heute 00:00 UTC bei sommerzeit Off-by-One möglich.
         Jetzt: nutze day.date (gesetzt bei Bot-Init = trading day).
  DSP-5: Fehlende Felder ergänzt (trades_completed_today, adds_executed,
         quick_exits, goal_reached, quarter_size_unlocked, spy_size_multiplier,
         cents_per_share_cumulative).
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def write_day_summary(day, spy_pct: float = 0.0) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    # Audit-Iter 28 (DSP-2): trading-day-date statt local-now-date.
    # DayState.date wird beim Bot-Init gesetzt = der trading day.
    today = getattr(day, "date", "") or datetime.now().strftime("%Y-%m-%d")
    out = RESULTS_DIR / f"{today}.json"
    payload = {
        "date": today,
        # PnL + Equity
        "realized_pnl": round(day.realized_pnl, 2),
        "peak_pnl": round(day.peak_pnl, 2),
        # Trade activity (Audit-Iter 28 (DSP-5): vorher fehlten diese)
        "trades_completed_today": getattr(day, "trades_completed_today", 0),
        "adds_executed": getattr(day, "adds_executed", 0),
        "quick_exits": getattr(day, "quick_exits", 0),
        "goal_reached": getattr(day, "goal_reached", False),
        "quarter_size_unlocked": getattr(day, "quarter_size_unlocked", False),
        "cents_per_share_cumulative": round(
            getattr(day, "cents_per_share_cumulative", 0.0), 4),
        # Pattern + Rejections
        "bars_received": day.bars_received,
        "patterns_detected": day.patterns_detected,
        "rejected_macd": day.patterns_rejected_macd,
        "rejected_fbo": day.patterns_rejected_fbo,
        "rejected_pullback_count": day.patterns_rejected_pullback_count,
        "rejected_size_zero": day.patterns_rejected_size_zero,
        "rejected_max_trades": getattr(day, "patterns_rejected_max_trades", 0),
        # Orders + Health
        "orders_submitted": day.orders_submitted,
        "orders_failed": day.orders_failed,
        "consecutive_losses": day.consecutive_losses,
        "spiral_locked": day.spiral_locked,
        "ws_reconnects": day.ws_reconnects,
        # Market regime
        "spy_pct": round(spy_pct, 3),
        "spy_size_multiplier": round(
            getattr(day, "spy_size_multiplier", 1.0), 2),
    }
    serialized = json.dumps(payload, indent=2)
    # Audit-Iter 28 (DSP-1): atomic write
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(str(tmp), str(out))
    return out
