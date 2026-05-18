"""Schreibt jede Iteration status.json — operator sieht Live-State ohne Log-Grep.

Felder: timestamp, account_equity, positions, watchlist, day-stats, last_health.

Audit-Iter 26 (2026-05-12) — Bug-Fixes SD-1/SD-2/SD-3:
  SD-1: atomic write via tmp+rename — read-during-write zeigte sonst
        partial JSON für externe Monitore.
  SD-2: silent except-Pass → throttled warning (alle 100 fails 1 log),
        damit disk-full nicht ewig unbemerkt bleibt.
  SD-3: trades_today war wrong field — DayState heißt trades_completed_today.
        Status JSON reportete IMMER 0 statt echter trade-count.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger("status")

STATUS_FILE = Path(__file__).parent / "status.json"
ALPACA_API_CALLS_LOG = Path(__file__).parent / "alpaca_api_calls.jsonl"
_write_fail_count = 0


def write_status(bot) -> None:
    """Wird von Bot.run() periodisch aufgerufen. Best-effort —
    schreibt atomic via tmp+rename, throttled warning bei wiederholtem Fail."""
    global _write_fail_count
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
        # Phase-56 (ChatGPT P0 follow-up 2026-05-15): expose live
        # diagnostics so a no-trade investigation can be answered in
        # one glance — rate-cap state, last call/block timestamps,
        # WS abuse counter, last bar timestamp, scanner status.
        alpaca_rate_per_min = 0
        ws_abuse_count = 0
        last_alpaca_call_ts = None
        last_alpaca_block_ts = None
        alpaca_blocked_count = 0
        try:
            from guarded_alpaca import current_rate_per_min
            alpaca_rate_per_min = current_rate_per_min()
        except Exception:
            pass
        try:
            from alpaca_ws_patch import get_ws_abuse_count
            ws_abuse_count = get_ws_abuse_count()
        except Exception:
            pass
        # Pull latest call timestamp from alpaca_api_calls.jsonl tail
        try:
            calls_log = ALPACA_API_CALLS_LOG
            if calls_log.exists():
                with open(calls_log, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    tail = f.read().decode("utf-8", errors="replace")
                for line in reversed(tail.splitlines()):
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        if last_alpaca_call_ts is None:
                            last_alpaca_call_ts = rec.get("ts")
                        if rec.get("status") == "blocked":
                            alpaca_blocked_count += 1
                            if last_alpaca_block_ts is None:
                                last_alpaca_block_ts = rec.get("ts")
                    except Exception:
                        pass
        except Exception:
            pass
        payload = {
            "ts": datetime.now().isoformat(),
            "account_equity": getattr(bot, "_last_equity", None),
            "realized_pnl": round(d.realized_pnl, 2),
            "peak_pnl": round(d.peak_pnl, 2),
            "spy_pct": round(d.spy_pct_today, 3),
            # Audit-Iter 26 (Bug SD-3): korrekter Field-Name. Fallback auf
            # trades_today behalten für Backwards-Compat falls jemand
            # an älteren Bots arbeitet.
            "trades_today": getattr(d, "trades_completed_today",
                                      getattr(d, "trades_today", 0)),
            "consecutive_losses": d.consecutive_losses,
            "spiral_locked": d.spiral_locked,
            "ws_reconnects": d.ws_reconnects,
            "positions_open": positions,
            "watchlist": watchlist,
            # Phase-56 diagnostics
            "alpaca_rate_per_min": alpaca_rate_per_min,
            "alpaca_rate_cap": 200,
            "last_alpaca_call_ts": last_alpaca_call_ts,
            "last_alpaca_block_ts": last_alpaca_block_ts,
            "alpaca_blocked_count": alpaca_blocked_count,
            "ws_abuse_count": ws_abuse_count,
            "last_ws_bar_ts": getattr(d, "last_ws_bar_ts", None),
            "last_tradingview_scan_status": getattr(
                d, "last_tradingview_scan_status", None),
            "last_no_trade_reason": getattr(d, "last_no_trade_reason", None),
            "scanner_source": getattr(d, "scanner_source", None),
            "fallback_used": getattr(d, "fallback_used", False),
        }
        serialized = json.dumps(payload, indent=2, default=str)
    except Exception as e:
        _write_fail_count += 1
        if _write_fail_count % 100 == 1:
            log.warning("status payload-build failed (#%d): %s",
                        _write_fail_count, e)
        return
    # Atomic write (Bug SD-1): write to tmp + rename
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        os.replace(str(tmp), str(STATUS_FILE))
        # Bei Success: counter reset (recovered)
        if _write_fail_count > 0:
            log.info("status write recovered after %d fails", _write_fail_count)
            _write_fail_count = 0
    except OSError as e:
        _write_fail_count += 1
        if _write_fail_count % 100 == 1:
            log.warning("status write failed (#%d): %s", _write_fail_count, e)
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
