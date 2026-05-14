"""Pre-Flight-Checks beim Daemon-Start.

Verhindert die Situation vom 2026-05-11: Bot lief 24 h obwohl der WS-Connect
defekt war, weil der Bug erst beim ersten Bar-Subscribe auftrat.

Jeder Check returns (ok: bool, msg: str). Fehler → abort vor sleep-loop.

Audit-Iter 13 (2026-05-12) — Bug-Fixes PF-1/PF-6/PF-7:
  PF-1: trading_blocked / account_blocked werden jetzt geprüft
  PF-6: min-equity-check (≥ $500 default)
  PF-7: leere keys werden früh erkannt (klare Fehlermeldung)
"""
from __future__ import annotations
import logging
import os
from typing import Optional

log = logging.getLogger("preflight")

MIN_EQUITY_USD = 500.0  # darunter macht Cameron-1%-Risk-Rule wenig Sinn


def check_alpaca_auth(api_key: str, api_secret: str,
                       min_equity: float = MIN_EQUITY_USD) -> tuple[bool, str]:
    """Audit-Iter 13:
      - PF-7: leere Keys früh erkennen
      - PF-1: trading_blocked + account_blocked prüfen
      - PF-6: min-equity-Check"""
    if not api_key or not api_secret:
        return False, "Alpaca-Auth FAIL: api_key/api_secret leer (env vars set?)"
    try:
        from alpaca.trading.client import TradingClient
        c = TradingClient(api_key, api_secret, paper=True)
        acc = c.get_account()
    except Exception as e:
        return False, f"Alpaca-Auth FAIL: {e}"
    # PF-1: Blocked-Account Detection
    if getattr(acc, "account_blocked", False):
        return False, "Alpaca-Auth FAIL: account_blocked=True (contact broker)"
    if getattr(acc, "trading_blocked", False):
        return False, "Alpaca-Auth FAIL: trading_blocked=True (PDT/margin issue)"
    # PF-6: min-Equity
    try:
        equity = float(acc.equity)
    except (TypeError, ValueError):
        return False, f"Alpaca-Auth FAIL: kann acc.equity nicht parsen ({acc.equity!r})"
    if equity < min_equity:
        return False, f"Alpaca-Auth FAIL: equity ${equity:,.2f} unter min ${min_equity:,.0f}"
    return True, f"Alpaca OK — equity ${equity:,.0f}"


def check_ws_init(api_key: str, api_secret: str) -> tuple[bool, str]:
    """Instanziiert StockDataStream einmal — fängt den 2026-05-11-Bug
    (str feed vs Enum) sofort beim Daemon-Start, nicht erst beim ersten Bar."""
    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
        ws = StockDataStream(api_key, api_secret, feed=DataFeed.IEX)
        ep = getattr(ws, "_endpoint", "")
        if "iex" not in ep:
            return False, f"WS-endpoint suspicious: {ep}"
        return True, f"WS-Init OK — endpoint={ep}"
    except Exception as e:
        return False, f"WS-Init FAIL: {e!r}"


def check_yfinance() -> tuple[bool, str]:
    try:
        import yfinance as yf
        df = yf.Ticker("SPY").history(period="1d", interval="5m")
        if df.empty:
            return False, "yfinance returned empty (rate-limited?)"
        return True, f"yfinance OK — {len(df)} bars"
    except Exception as e:
        return False, f"yfinance FAIL: {e}"


def run_preflight(api_key: str, api_secret: str, *, skip_yfinance: bool = False,
                  yfinance_required: bool = True) -> bool:
    """Review-V2 P2.4: yfinance is the live-scanner's pillar-4 data source.
    yfinance-fail must block (or trigger degraded-mode warning), not silently
    pass. After two_source_scan integration (P1.2), Alpaca is the fallback;
    we now log a clear "degraded mode" instead of silently continuing.
    """
    log.info("=" * 60)
    log.info("PRE-FLIGHT CHECKS")
    log.info("=" * 60)
    checks = [
        ("Alpaca-Auth", lambda: check_alpaca_auth(api_key, api_secret)),
        ("WS-Init",     lambda: check_ws_init(api_key, api_secret)),
    ]
    if not skip_yfinance:
        checks.append(("yfinance", check_yfinance))
    all_ok = True
    yfinance_status = None
    for name, fn in checks:
        ok, msg = fn()
        marker = "OK" if ok else "FAIL"
        log.info("  [%s] %s — %s", marker, name, msg)
        if not ok:
            if name == "yfinance":
                yfinance_status = (ok, msg)
                if yfinance_required:
                    # Two-source-fallback can rescue, but we want operator
                    # to KNOW we're degraded — not silent.
                    log.warning("  yfinance FAILED — degraded scanner mode active")
                    log.warning("  (two_source_scan will use Alpaca fallback for "
                                "missing symbols; pilot-4 may be incomplete)")
            else:
                all_ok = False
    log.info("=" * 60)
    if not all_ok:
        log.info("PRE-FLIGHT: FAIL — daemon will not start")
    elif yfinance_status and not yfinance_status[0]:
        log.info("PRE-FLIGHT: PASS (degraded — yfinance down, Alpaca-fallback only)")
    else:
        log.info("PRE-FLIGHT: PASS")
    log.info("=" * 60)
    return all_ok
