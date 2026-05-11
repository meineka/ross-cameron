"""End-of-Day Summary → results/YYYY-MM-DD.json für historische Trends."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def write_day_summary(day, spy_pct: float = 0.0) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out = RESULTS_DIR / f"{today}.json"
    payload = {
        "date": today,
        "realized_pnl": round(day.realized_pnl, 2),
        "peak_pnl": round(day.peak_pnl, 2),
        "bars_received": day.bars_received,
        "patterns_detected": day.patterns_detected,
        "rejected_macd": day.patterns_rejected_macd,
        "rejected_fbo": day.patterns_rejected_fbo,
        "rejected_pullback_count": day.patterns_rejected_pullback_count,
        "rejected_size_zero": day.patterns_rejected_size_zero,
        "orders_submitted": day.orders_submitted,
        "orders_failed": day.orders_failed,
        "consecutive_losses": day.consecutive_losses,
        "spiral_locked": day.spiral_locked,
        "ws_reconnects": day.ws_reconnects,
        "spy_pct": round(spy_pct, 3),
        # Alpha vs SPY (heuristisch — Bot-PnL is $, SPY is %)
        "alpha_proxy": round(day.realized_pnl - spy_pct * 1000, 2),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
