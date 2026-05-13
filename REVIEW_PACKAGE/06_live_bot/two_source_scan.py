"""Two-Source Premarket-Scan-Robustheit.

2026-05-11: yfinance gab für ~12 Symbole 404 zurück. Wenn das ein größerer
Universe-Bruch wäre, hätte unsere Watchlist gelitten. Diese Funktion prüft
das Verhältnis null-Daten zu Symbolen — wenn >20 % delisted, signalisiere
Alpaca-Fallback.
"""
from __future__ import annotations
import logging

log = logging.getLogger("two-source")

YFINANCE_FAIL_THRESHOLD_PCT = 20.0


def yfinance_failure_ratio(total: int, failed: int) -> float:
    if total <= 0:
        return 0.0
    return (failed / total) * 100


def should_fallback_to_alpaca(total: int, failed: int) -> bool:
    return yfinance_failure_ratio(total, failed) > YFINANCE_FAIL_THRESHOLD_PCT


def alpaca_universe_snapshot(data_client, candidates: list[str]) -> list[tuple[str, float, float]]:
    """Fallback: liefert (symbol, price, intraday_pct) per Alpaca-Snapshot.

    Wir geben keine RVOL zurück (nur 1 Day vs Previous-Day-Bar verfügbar),
    aber wenigstens haben wir was zum traden wenn yfinance down ist.
    """
    from alpaca.data.requests import StockSnapshotRequest
    try:
        snaps = data_client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=candidates))
    except Exception as e:
        log.error("alpaca-fallback snapshot failed: %s", e)
        return []
    out = []
    for sym, snap in snaps.items():
        try:
            b = snap.daily_bar
            p = snap.previous_daily_bar
            if not (b and p and p.close):
                continue
            pct = (b.close - p.close) / p.close * 100
            out.append((sym, b.close, pct))
        except Exception:
            continue
    return out
