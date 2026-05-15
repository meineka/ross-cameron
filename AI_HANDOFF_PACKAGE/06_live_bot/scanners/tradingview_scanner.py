"""Phase-28: TradingView Screener as Cameron-Bot's primary candidate source.

Replaces yfinance.download(batch, interval='1d') for the premarket-scan
"give me the top-N gappers" question. yfinance had three problems:
  1. YFRateLimitError after a few hundred calls
  2. Sparse / stale data for micro-caps (Cameron's universe)
  3. No native premarket_change / premarket_volume — we faked them

TradingView's public scanner exposes the SAME query engine that runs
their web UI, including real premarket fields. No auth, no API key,
no rate limit. We use the `tradingview-screener` Python package as the
HTTP wrapper.

Public surface:
  scan_cameron_candidates(top_n=200) -> list[dict]
    Returns up to top_n rows with fields:
      ticker, exchange, close, change_pct,
      volume, rvol_proxy,
      premarket_change, premarket_volume,
      float_shares,
      source ("tradingview" | "alpaca_fallback")
    Sorted by premarket_change descending.

  scan_cameron_candidates_alpaca_fallback(client, top_n=50) -> list[dict]
    Used when TradingView is unreachable. Calls Alpaca's
    /v1beta1/screener/stocks/movers and dedupes by symbol.
"""
from __future__ import annotations
import logging
import time
from typing import Optional

log = logging.getLogger("tv-scanner")

# Cameron's defaults — tuned in the trader-loop iterations (see
# 04_backtest/trader_perspective_iterations.md). Caller can override.
DEFAULT_FILTERS = {
    "premarket_change_min_pct": 5.0,
    "rvol_min": 3.0,
    "price_min": 2.0,
    "price_max": 20.0,
    "float_max_shares": 50_000_000,   # generous; bot has its own 10M filter
    "exchanges": ("NASDAQ", "NYSE", "AMEX"),
}


def scan_cameron_candidates(top_n: int = 200,
                              *, premarket_change_min_pct: float | None = None,
                              rvol_min: float | None = None,
                              price_min: float | None = None,
                              price_max: float | None = None,
                              float_max_shares: int | None = None,
                              exchanges: tuple[str, ...] | None = None,
                              md_logger=None,
                              ) -> list[dict]:
    """Query TradingView for top-N Cameron-conformant candidates.

    Returns a list of dicts (newest run last). Each dict has at minimum:
      ticker, exchange, close, change_pct, volume, rvol_proxy,
      premarket_change, premarket_volume, float_shares, source

    On TradingView error: returns [] (caller decides whether to fall
    back to Alpaca or yfinance). Errors are logged but never raised.
    """
    pmin = premarket_change_min_pct if premarket_change_min_pct is not None \
            else DEFAULT_FILTERS["premarket_change_min_pct"]
    rmin = rvol_min if rvol_min is not None else DEFAULT_FILTERS["rvol_min"]
    p_lo = price_min if price_min is not None else DEFAULT_FILTERS["price_min"]
    p_hi = price_max if price_max is not None else DEFAULT_FILTERS["price_max"]
    fmax = float_max_shares if float_max_shares is not None \
            else DEFAULT_FILTERS["float_max_shares"]
    exch = exchanges if exchanges is not None else DEFAULT_FILTERS["exchanges"]

    t0 = time.perf_counter()
    try:
        from tradingview_screener import Query, Column
    except ImportError as e:
        log.warning("tradingview-screener not installed: %s — returning []", e)
        if md_logger is not None:
            md_logger.log_call(source="tradingview", call="scan",
                                status="error", error_class="ImportError")
        return []

    try:
        q = (Query()
            .select(
                'name', 'close', 'change', 'volume',
                'relative_volume_10d_calc',
                'premarket_change', 'premarket_volume',
                'float_shares_outstanding_current',
                'exchange',
            )
            .where(
                Column('premarket_change') > pmin,
                Column('relative_volume_10d_calc') > rmin,
                Column('close').between(p_lo, p_hi),
                Column('float_shares_outstanding_current') < fmax,
                Column('exchange').isin(list(exch)),
            )
            .order_by('premarket_change', ascending=False)
            .limit(top_n))
        count, df = q.get_scanner_data()
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log.info("TV-scanner: %d matches in %.0fms (limit=%d, filters: "
                 "pmkt>=%.1f%%, rvol>=%.1fx, $%.2f-$%.2f, float<%d)",
                 count, latency_ms, top_n, pmin, rmin, p_lo, p_hi, fmax)
        rows = _df_to_rows(df)
        if md_logger is not None:
            md_logger.log_call(
                source="tradingview", call="scan", status="ok",
                latency_ms=latency_ms,
                symbol_count=len(rows),
                extra={"total_matches": count, "limit": top_n},
            )
        return rows
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log.warning("TV-scanner failed: %s — caller should fallback", e)
        if md_logger is not None:
            md_logger.log_call(
                source="tradingview", call="scan", status="error",
                latency_ms=latency_ms,
                error_class=type(e).__name__,
                extra={"error": str(e)[:200]},
            )
        return []


def _df_to_rows(df) -> list[dict]:
    """Translate TradingView's pandas-DataFrame output to plain dicts
    with stable field names (decoupled from TV's column-name churn)."""
    out: list[dict] = []
    if df is None or len(df) == 0:
        return out
    for r in df.itertuples():
        ticker_raw = getattr(r, "ticker", None) or getattr(r, "name", None)
        if not ticker_raw:
            continue
        # TV returns "NASDAQ:AAPL" in `ticker`; strip exchange prefix
        ticker = ticker_raw.split(":", 1)[-1].strip().upper()
        exchange = getattr(r, "exchange", None)
        if not exchange and ":" in ticker_raw:
            exchange = ticker_raw.split(":", 1)[0]
        try:
            row = {
                "ticker": ticker,
                "exchange": exchange,
                "close": _safe_float(getattr(r, "close", None)),
                "change_pct": _safe_float(getattr(r, "change", None)),
                "volume": _safe_float(getattr(r, "volume", None)),
                "rvol_proxy": _safe_float(
                    getattr(r, "relative_volume_10d_calc", None)),
                "premarket_change": _safe_float(
                    getattr(r, "premarket_change", None)),
                "premarket_volume": _safe_float(
                    getattr(r, "premarket_volume", None)),
                "float_shares": _safe_float(
                    getattr(r, "float_shares_outstanding_current", None)),
                "source": "tradingview",
            }
            out.append(row)
        except Exception:
            continue
    return out


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def scan_cameron_candidates_alpaca_fallback(client, top_n: int = 50,
                                              *, md_logger=None) -> list[dict]:
    """Phase-28 fallback: when TradingView is unreachable, use Alpaca's
    /v1beta1/screener/stocks/movers endpoint. Returns up to top_n
    gainers (no loser-side; Cameron is long-only).

    Tags returned rows with source="alpaca_fallback" so postmortem can
    distinguish primary-vs-fallback decisions.
    """
    t0 = time.perf_counter()
    try:
        # alpaca-py exposes this via MarketMoversRequest
        try:
            from alpaca.data.requests import MarketMoversRequest
            req = MarketMoversRequest(top=min(top_n, 50))
            movers = client.get_market_movers(req)
        except (ImportError, AttributeError):
            # Older alpaca-py: try raw HTTP. Skip silently if neither works.
            log.warning("Alpaca-py MarketMoversRequest not available — "
                         "fallback unavailable, returning []")
            return []
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        gainers = getattr(movers, "gainers", []) or []
        rows: list[dict] = []
        for m in gainers[:top_n]:
            sym = getattr(m, "symbol", None)
            if not sym:
                continue
            rows.append({
                "ticker": sym.upper(),
                "exchange": None,
                "close": _safe_float(getattr(m, "price", None)),
                "change_pct": _safe_float(getattr(m, "percent_change", None)),
                "volume": _safe_float(getattr(m, "volume", None)),
                "rvol_proxy": None,
                "premarket_change": _safe_float(
                    getattr(m, "percent_change", None)),
                "premarket_volume": None,
                "float_shares": None,
                "source": "alpaca_fallback",
            })
        log.info("Alpaca-fallback movers: %d rows in %.0fms",
                 len(rows), latency_ms)
        if md_logger is not None:
            md_logger.log_call(
                source="alpaca", call="movers", status="ok",
                latency_ms=latency_ms, symbol_count=len(rows),
            )
        return rows
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        log.warning("Alpaca-fallback movers failed: %s", e)
        if md_logger is not None:
            md_logger.log_call(
                source="alpaca", call="movers", status="error",
                latency_ms=latency_ms, error_class=type(e).__name__,
                extra={"error": str(e)[:200]},
            )
        return []


def scan_cameron_top_candidates(top_n: int = 200,
                                  *, alpaca_client=None,
                                  md_logger=None,
                                  **filter_overrides) -> list[dict]:
    """Public entrypoint: try TradingView first, fall back to Alpaca
    if TV returns []. Returns merged top-N rows. Caller does NOT need
    to know which source served.

    Use this from bot.py's premarket scan path.
    """
    rows = scan_cameron_candidates(top_n=top_n, md_logger=md_logger,
                                     **filter_overrides)
    if rows:
        return rows
    log.warning("TradingView returned 0 rows — attempting Alpaca fallback")
    if alpaca_client is None:
        log.warning("No alpaca_client supplied — returning empty result")
        return []
    return scan_cameron_candidates_alpaca_fallback(
        alpaca_client, top_n=top_n, md_logger=md_logger)
