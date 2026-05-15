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

Phase-16 (ChatGPT-08:11 #1): three additions to satisfy P1.1:
  - Every candidate gets a `passed: bool` + `reject_reasons: list[str]`
    in its result row, so the scanner becomes diagnosable per-candidate.
    `scan_alpaca_premarket_with_reasons()` returns ALL rows (passed +
    rejected) for postmortem visibility; the legacy
    `scan_alpaca_premarket()` still returns only-passed for live use.
  - `scan_extended_hours_bars()` pulls real premarket (04:00-09:30 ET)
    minute bars from Alpaca for each candidate and computes:
      premarket_volume_today vs avg_premarket_volume_N_days  -> RVOL
      premarket_high / low / vwap for context
  - `compute_premarket_rvol()` wires the bar data into the evaluator
    so rvol is no longer a daily-volume proxy.

Falls back to yfinance for symbols Alpaca doesn't cover (rare for US
common stocks but possible for OTC/PINK tickers).
"""
from __future__ import annotations
import logging
from datetime import datetime, time as dtime, timezone, timedelta
from typing import Optional

log = logging.getLogger("premarket-v2")

# Thresholds — tunable
MIN_PREMARKET_GAP_PCT = 5.0  # Cameron-Pillar-4 minimum
MAX_QUOTE_AGE_SECONDS = 600  # 10 min: stale beyond this
MAX_SPREAD_PCT_OF_MID = 5.0  # >5% spread = illiquid
MIN_PREMARKET_VOLUME = 1_000  # daily-bar volume (proxy until extended-hours)
# Phase-16: real premarket RVOL thresholds (extended-hours bars)
MIN_PREMARKET_RVOL = 2.0  # today's premarket vol / 20d-avg premarket vol
PREMARKET_RVOL_LOOKBACK_DAYS = 20
PREMARKET_START_ET = dtime(4, 0)   # 04:00 ET — extended-hours session start
PREMARKET_END_ET = dtime(9, 30)    # 09:30 ET — RTH open


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


# ─── Phase-16 (ChatGPT-08:11 #1): reject-reasons + extended-hours bars ──────

def _evaluate_snapshot_with_reasons(sym: str, snap, now_utc: datetime,
                                      mode: str = "strict") -> dict:
    """Phase-16: like _evaluate_snapshot but ALWAYS returns a row with
    {passed: bool, reject_reasons: list[str]}. This is the diagnosable
    flavor for postmortem / scanner-debug; pass results into the bot
    via passed-filter."""
    reasons: list[str] = []
    if snap is None:
        return {"ticker": sym, "passed": False,
                "reject_reasons": ["no_snapshot"]}
    daily = getattr(snap, "daily_bar", None)
    prev = getattr(snap, "previous_daily_bar", None)
    latest_trade = getattr(snap, "latest_trade", None)
    latest_quote = getattr(snap, "latest_quote", None)

    prev_close = None
    if prev and getattr(prev, "close", None):
        prev_close = float(prev.close)
    elif mode == "strict":
        reasons.append("no_previous_close")

    last_price = None
    age_s = float("inf")
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
    elif mode == "strict":
        reasons.append("no_latest_trade")
        last_price = float(daily.close) if daily and getattr(daily, "close", None) else None

    if mode == "strict" and age_s > MAX_QUOTE_AGE_SECONDS:
        reasons.append(f"trade_stale_{age_s:.0f}s")

    gap_pct = None
    if prev_close is not None and last_price is not None and prev_close > 0:
        gap_pct = (last_price - prev_close) / prev_close * 100
    if mode == "strict" and gap_pct is not None and gap_pct < MIN_PREMARKET_GAP_PCT:
        reasons.append(f"gap_{gap_pct:.2f}%_under_{MIN_PREMARKET_GAP_PCT}%")

    spread_pct = None
    if (latest_quote
            and getattr(latest_quote, "bid_price", None)
            and getattr(latest_quote, "ask_price", None)):
        bid = float(latest_quote.bid_price)
        ask = float(latest_quote.ask_price)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid * 100
            if mode == "strict" and spread_pct > MAX_SPREAD_PCT_OF_MID:
                reasons.append(f"spread_{spread_pct:.2f}%_over_{MAX_SPREAD_PCT_OF_MID}%")

    volume = float(daily.volume) if daily and getattr(daily, "volume", None) else 0.0
    if mode == "strict" and volume < MIN_PREMARKET_VOLUME:
        reasons.append(f"vol_{volume:.0f}_under_{MIN_PREMARKET_VOLUME}")

    return {
        "ticker": sym,
        "last_price": last_price,
        "prev_close": prev_close,
        "gap_pct": gap_pct,
        "spread_pct": spread_pct,
        "volume": volume,
        "quote_age_s": age_s if age_s != float("inf") else None,
        "passed": len(reasons) == 0,
        "reject_reasons": reasons,
    }


def scan_alpaca_premarket_with_reasons(data_client,
                                         candidate_symbols: list[str],
                                         *, mode: str = "strict") -> list[dict]:
    """Phase-16: like scan_alpaca_premarket but returns ALL candidates
    (passed + rejected) with per-row reject_reasons so the postmortem
    can show exactly why each candidate didn't make the watchlist."""
    if mode not in ("off", "soft", "strict"):
        raise ValueError(f"mode must be off|soft|strict, got {mode!r}")
    from alpaca.data.requests import StockSnapshotRequest
    out: list[dict] = []
    now_utc = datetime.now(timezone.utc)
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
            # Emit reject rows for the whole batch so postmortem sees them
            for sym in batch:
                out.append({"ticker": sym, "passed": False,
                            "reject_reasons": [f"batch_fetch_failed: {e}"]})
            continue
    for sym in candidate_symbols:
        snap = snaps_all.get(sym)
        try:
            out.append(_evaluate_snapshot_with_reasons(sym, snap, now_utc, mode))
        except Exception as e:
            log.debug("evaluate %s err: %s", sym, e)
            out.append({"ticker": sym, "passed": False,
                        "reject_reasons": [f"evaluator_error: {type(e).__name__}"]})
    passed = sum(1 for r in out if r.get("passed"))
    log.info("Alpaca premarket-scan-with-reasons: %d/%d passed (mode=%s)",
             passed, len(candidate_symbols), mode)
    return out


def _today_premarket_window_utc(now_utc: datetime | None = None) -> tuple[datetime, datetime]:
    """Phase-16: return (start_utc, end_utc) covering today's premarket
    session 04:00-09:30 America/New_York. Uses a fixed UTC-5 offset
    fallback if zoneinfo isn't available — good enough for premarket
    bar fetch which is bucket-level not millisecond-level."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
    except Exception:
        ny = timezone(timedelta(hours=-5))
    ny_now = now_utc.astimezone(ny)
    start_ny = ny_now.replace(hour=PREMARKET_START_ET.hour,
                                minute=PREMARKET_START_ET.minute,
                                second=0, microsecond=0)
    end_ny = ny_now.replace(hour=PREMARKET_END_ET.hour,
                              minute=PREMARKET_END_ET.minute,
                              second=0, microsecond=0)
    return start_ny.astimezone(timezone.utc), end_ny.astimezone(timezone.utc)


def scan_extended_hours_bars(data_client, symbols: list[str],
                               *, lookback_days: int = PREMARKET_RVOL_LOOKBACK_DAYS,
                               now_utc: datetime | None = None) -> dict[str, dict]:
    """Phase-16: pull extended-hours 1-min bars for the premarket window
    (04:00-09:30 ET) for `symbols`, today and the last `lookback_days`
    trading days. Returns:
      {symbol: {
          "premarket_volume_today": float,
          "avg_premarket_volume": float,
          "premarket_rvol": float,            # today / avg
          "premarket_high": float,
          "premarket_low": float,
          "premarket_vwap": float,
          "bars_today": int,                  # raw bar count today
       }}
    Missing data → symbol key omitted (caller can default).
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    today_start_utc, today_end_utc = _today_premarket_window_utc(now_utc)
    fetch_start = today_start_utc - timedelta(days=lookback_days + 7)  # weekend pad

    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=fetch_start,
            end=today_end_utc,
            feed="iex",  # paper-friendly
        )
        bars_obj = data_client.get_stock_bars(req)
    except Exception as e:
        log.warning("scan_extended_hours_bars: fetch failed: %s", e)
        return {}

    # Alpaca SDK returns BarSet with .data[symbol] = [Bar, ...]
    by_sym = getattr(bars_obj, "data", None) or {}
    out: dict[str, dict] = {}

    for sym in symbols:
        bars = by_sym.get(sym) or []
        if not bars:
            continue
        today_bars = []
        prior_days_bars: dict[str, list] = {}  # date_str -> bars
        for b in bars:
            ts = getattr(b, "timestamp", None)
            if ts is None:
                continue
            # Bucket per-NY-date and filter premarket window per day
            try:
                ny_dt = ts.astimezone(_today_premarket_window_utc.__defaults__[0]
                                       if False else timezone(timedelta(hours=-5)))
            except Exception:
                continue
            t_ny = ny_dt.time()
            if not (PREMARKET_START_ET <= t_ny < PREMARKET_END_ET):
                continue
            d_key = ny_dt.strftime("%Y-%m-%d")
            today_key = (now_utc.astimezone(timezone(timedelta(hours=-5)))
                          .strftime("%Y-%m-%d"))
            if d_key == today_key:
                today_bars.append(b)
            else:
                prior_days_bars.setdefault(d_key, []).append(b)

        # Compute today's stats
        pm_vol_today = sum(float(getattr(b, "volume", 0) or 0) for b in today_bars)
        if today_bars:
            highs = [float(getattr(b, "high", 0) or 0) for b in today_bars]
            lows = [float(getattr(b, "low", 0) or 0) for b in today_bars if getattr(b, "low", None)]
            pv_sum = sum(((float(getattr(b, "high", 0) or 0) +
                            float(getattr(b, "low", 0) or 0) +
                            float(getattr(b, "close", 0) or 0)) / 3.0
                           * float(getattr(b, "volume", 0) or 0))
                          for b in today_bars)
            pm_high = max(highs) if highs else None
            pm_low = min(lows) if lows else None
            pm_vwap = (pv_sum / pm_vol_today) if pm_vol_today > 0 else None
        else:
            pm_high = pm_low = pm_vwap = None

        # Average premarket volume across prior trading days
        daily_vols = [sum(float(getattr(b, "volume", 0) or 0) for b in day_bars)
                       for day_bars in prior_days_bars.values()]
        # Keep last N
        daily_vols = daily_vols[-lookback_days:] if len(daily_vols) > lookback_days else daily_vols
        avg_pm_vol = (sum(daily_vols) / len(daily_vols)) if daily_vols else 0.0
        rvol = (pm_vol_today / avg_pm_vol) if avg_pm_vol > 0 else None

        out[sym] = {
            "premarket_volume_today": pm_vol_today,
            "avg_premarket_volume": avg_pm_vol,
            "premarket_rvol": rvol,
            "premarket_high": pm_high,
            "premarket_low": pm_low,
            "premarket_vwap": pm_vwap,
            "bars_today": len(today_bars),
        }
    return out


def merge_premarket_rvol_into_rows(rows: list[dict],
                                    bar_stats: dict[str, dict],
                                    *, mode: str = "strict") -> list[dict]:
    """Phase-16: enrich the rows from scan_alpaca_premarket_with_reasons
    with real premarket RVOL + reject those failing MIN_PREMARKET_RVOL.

    Modifies and returns `rows`. Adds these fields per row:
      premarket_volume_today, avg_premarket_volume, premarket_rvol,
      premarket_high, premarket_low, premarket_vwap, bars_today
    Adds a reject_reason if rvol below MIN_PREMARKET_RVOL in strict mode.
    """
    for r in rows:
        sym = r.get("ticker")
        stats = bar_stats.get(sym) or {}
        for k in ("premarket_volume_today", "avg_premarket_volume",
                   "premarket_rvol", "premarket_high", "premarket_low",
                   "premarket_vwap", "bars_today"):
            r[k] = stats.get(k)
        rvol = stats.get("premarket_rvol")
        if mode == "strict":
            if not stats:
                r.setdefault("reject_reasons", []).append("no_premarket_bars")
                r["passed"] = False
            elif rvol is None:
                r.setdefault("reject_reasons", []).append("rvol_unknown")
                r["passed"] = False
            elif rvol < MIN_PREMARKET_RVOL:
                r.setdefault("reject_reasons", []).append(
                    f"rvol_{rvol:.2f}_under_{MIN_PREMARKET_RVOL}")
                r["passed"] = False
    return rows
