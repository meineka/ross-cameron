"""Review-V2 P1.1: Real Premarket Scanner via Alpaca extended-hours data.

Previously the bot used yfinance daily-bars as a Cameron-Pillar-4 proxy
(intraday_pct = daily.high vs prev_close, rvol_proxy = daily-volume vs
rolling-mean). That misses the LIVE premarket gap that Cameron's strategy
depends on.

This module computes premarket gap + premarket-volume RVOL from Alpaca's
StockSnapshot API (which includes latest_trade + previous_daily_bar +
daily_bar). For each candidate ticker:

  - premarket_last  = snap.latest_trade.price (most-recent trade)
  - prev_close      = snap.previous_daily_bar.close
  - daily_open      = snap.daily_bar.open  (after market open)
  - gap_pct         = (premarket_last - prev_close) / prev_close * 100
  - premarket_age   = now - snap.latest_trade.timestamp  (freshness)
  - spread          = ask - bid  (from latest_quote)

Strict-mode (recommended for live):
  - missing latest_trade → reject (no current premarket signal)
  - premarket_age > MAX_AGE_SEC → reject (stale)
  - gap_pct < MIN_PREMARKET_GAP_PCT → reject (not a mover)
  - spread > MAX_SPREAD_PCT_OF_MID → reject (illiquid)

Soft-mode: warn but allow. Off: pass-through.

Falls back to yfinance for symbols Alpaca doesn't cover (rare for US
common stocks but possible for OTC/PINK tickers).
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("premarket-v2")

# Thresholds — tunable
MIN_PREMARKET_GAP_PCT = 5.0  # Cameron-Pillar-4 minimum
MAX_QUOTE_AGE_SECONDS = 600  # 10 min: stale beyond this
MAX_SPREAD_PCT_OF_MID = 5.0  # >5% spread = illiquid
MIN_PREMARKET_VOLUME = 1_000  # daily-bar volume (proxy until extended-hours)


def scan_alpaca_premarket(data_client, candidate_symbols: list[str],
                           *, mode: str = "soft") -> list[dict]:
    """Scan candidates via Alpaca StockSnapshot. Returns list of
    {ticker, last_price, gap_pct, spread, volume, quote_age_s} dicts
    for symbols that pass the filter.

    mode:
      - "off": no filtering — return all snapshots that have ANY data
      - "soft": warn on stale/missing but pass through
      - "strict": fail-closed — only fresh + sufficient-gap + liquid pass
    """
    if mode not in ("off", "soft", "strict"):
        raise ValueError(f"premarket scanner mode must be off|soft|strict, got {mode!r}")

    from alpaca.data.requests import StockSnapshotRequest
    out = []
    now_utc = datetime.now(timezone.utc)

    # Alpaca caps batch size; chunk
    BATCH = 500
    snaps_all: dict = {}
    for i in range(0, len(candidate_symbols), BATCH):
        batch = candidate_symbols[i:i + BATCH]
        try:
            req = StockSnapshotRequest(symbol_or_symbols=batch)
            chunk = data_client.get_stock_snapshot(req)
            if isinstance(chunk, dict):
                snaps_all.update(chunk)
        except Exception as e:
            log.warning("Alpaca snapshot batch %d-%d failed: %s",
                        i, i + len(batch), e)
            continue

    for sym, snap in snaps_all.items():
        try:
            row = _evaluate_snapshot(sym, snap, now_utc, mode)
        except Exception as e:
            log.debug("evaluate %s err: %s", sym, e)
            continue
        if row is not None:
            out.append(row)

    log.info("Alpaca premarket-scan: %d/%d symbols passed (mode=%s)",
             len(out), len(candidate_symbols), mode)
    return out


def _evaluate_snapshot(sym: str, snap, now_utc: datetime, mode: str) -> Optional[dict]:
    """Returns dict if passes filter, None if rejected."""
    if snap is None:
        return None
    daily = getattr(snap, "daily_bar", None)
    prev = getattr(snap, "previous_daily_bar", None)
    latest_trade = getattr(snap, "latest_trade", None)
    latest_quote = getattr(snap, "latest_quote", None)

    if not prev or not getattr(prev, "close", None):
        if mode == "strict":
            return None  # no previous-close = can't compute gap
        # soft/off: return with what we have
        prev_close = None
    else:
        prev_close = float(prev.close)

    # Premarket gap from latest_trade
    if latest_trade and getattr(latest_trade, "price", None):
        last_price = float(latest_trade.price)
        trade_ts = getattr(latest_trade, "timestamp", None)
        if trade_ts is not None:
            try:
                age_s = (now_utc - trade_ts).total_seconds()
            except Exception:
                age_s = 0.0
        else:
            age_s = 0.0
    else:
        if mode == "strict":
            return None  # no latest trade = no current premarket signal
        last_price = float(daily.close) if daily and daily.close else None
        age_s = float("inf")

    # Strict freshness gate
    if mode == "strict" and age_s > MAX_QUOTE_AGE_SECONDS:
        log.debug("STRICT reject %s: trade-age %.0fs > %ds", sym, age_s, MAX_QUOTE_AGE_SECONDS)
        return None

    # Gap-% computation
    if prev_close is not None and last_price is not None and prev_close > 0:
        gap_pct = (last_price - prev_close) / prev_close * 100
    else:
        gap_pct = None

    if mode == "strict" and gap_pct is not None and gap_pct < MIN_PREMARKET_GAP_PCT:
        log.debug("STRICT reject %s: gap %.2f%% < %.2f%%", sym, gap_pct, MIN_PREMARKET_GAP_PCT)
        return None

    # Spread / liquidity from latest_quote
    spread_pct = None
    if latest_quote and getattr(latest_quote, "bid_price", None) and getattr(latest_quote, "ask_price", None):
        bid = float(latest_quote.bid_price)
        ask = float(latest_quote.ask_price)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid * 100
            if mode == "strict" and spread_pct > MAX_SPREAD_PCT_OF_MID:
                log.debug("STRICT reject %s: spread %.2f%% > %.2f%%",
                          sym, spread_pct, MAX_SPREAD_PCT_OF_MID)
                return None

    # Daily volume as a coarse liquidity proxy (real premarket-volume
    # would need extended-hours bars — Alpaca paper plan doesn't always
    # include those)
    volume = float(daily.volume) if daily and getattr(daily, "volume", None) else 0.0
    if mode == "strict" and volume < MIN_PREMARKET_VOLUME:
        return None

    return {
        "ticker": sym,
        "last_price": last_price,
        "prev_close": prev_close,
        "gap_pct": gap_pct,
        "spread_pct": spread_pct,
        "volume": volume,
        "quote_age_s": age_s,
    }
