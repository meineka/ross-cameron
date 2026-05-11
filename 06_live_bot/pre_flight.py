"""Pre-Flight-Checks beim Daemon-Start.

Verhindert die Situation vom 2026-05-11: Bot lief 24 h obwohl der WS-Connect
defekt war, weil der Bug erst beim ersten Bar-Subscribe auftrat.

Jeder Check returns (ok: bool, msg: str). Fehler → abort vor sleep-loop.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

log = logging.getLogger("preflight")


def check_alpaca_auth(api_key: str, api_secret: str) -> tuple[bool, str]:
    try:
        from alpaca.trading.client import TradingClient
        c = TradingClient(api_key, api_secret, paper=True)
        acc = c.get_account()
        return True, f"Alpaca OK — equity ${float(acc.equity):,.0f}"
    except Exception as e:
        return False, f"Alpaca-Auth FAIL: {e}"


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


def run_preflight(api_key: str, api_secret: str, *, skip_yfinance: bool = False) -> bool:
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
    for name, fn in checks:
        ok, msg = fn()
        marker = "OK" if ok else "FAIL"
        log.info("  [%s] %s — %s", marker, name, msg)
        if not ok:
            all_ok = False
    log.info("=" * 60)
    log.info("PRE-FLIGHT: %s", "PASS" if all_ok else "FAIL — daemon will not start")
    log.info("=" * 60)
    return all_ok
