"""
backtest_bull_flag_v2.py — Iteration 2 mit allen identifizierten Fixes:

  1. VWAP-Session-Reset (per Trading-Tag, nicht cumulative)
  2. MACD 12/26/9 — Cross-Down als Entry-Veto + Hard-Exit-Trigger
  3. 5-Indikator-False-Breakout-Filter (constraints.yaml#false_breakout_filter)
  4. Slippage-Modell (1¢ Spread auf Entry + Stop)
  5. Pre-/Post-Market-Filter (nur RTH-Bars für Patterns)
  6. Konfigurierbare Filter-Strikte: --strict / --moderate / --loose

Usage:
  python backtest_bull_flag_v2.py                       # default: --moderate
  python backtest_bull_flag_v2.py --strict              # 0 false-breakout-hits erlaubt
  python backtest_bull_flag_v2.py --loose --limit 15    # ≤2 hits erlaubt
  python backtest_bull_flag_v2.py --ticker VNRX --debug # 1 Ticker mit Detail-Logging
"""

from __future__ import annotations

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import argparse
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data_pilot"

# ── Constraints (mirrors constraints.yaml) ─────────────────────────────────
POLE_CANDLES_MIN = 3
POLE_CANDLES_MAX = 7
POLE_MOVE_MIN_PCT = 5.0
POLE_TOPPING_TAIL_MAX_RATIO = 0.4
FLAG_CANDLES_MIN = 1
FLAG_CANDLES_MAX = 3
FLAG_RETRACE_MAX_PCT = 50.0
BREAKOUT_VOL_FACTOR_MIN = 1.5

# Slippage (legacy — kept for backward-compat where used standalone)
SLIPPAGE_ENTRY_CENTS = 0.01
SLIPPAGE_STOP_CENTS = 0.01

# Phase-80 (2026-05-19, user request "auch im backtest spread annehmen"):
# Realistic bid/ask spread model for small-cap penny stocks. Real spread
# observed on Alpaca paper for $2-20 small-caps: 30-80 bps (0.30%-0.80%)
# during RTH, 100-200 bps in pre/post-market. We use a CONSERVATIVE 50 bps
# (0.5%) average to avoid back-test over-optimism. Entry pays the ask
# (+half-spread), exit hits the bid (-half-spread).
SPREAD_BPS = 50              # 50 basis points = 0.50% total bid-ask spread
HALF_SPREAD = SPREAD_BPS / 20000.0   # 0.0025 = 0.25% added to entry price


def entry_with_spread(planned_entry: float) -> float:
    """Add half-spread + cent-slippage to a planned long entry (limit/
    breakout). The bot pays the ask, which is higher than the planned
    breakout price."""
    return planned_entry * (1.0 + HALF_SPREAD) + SLIPPAGE_ENTRY_CENTS


def exit_with_spread(planned_exit: float) -> float:
    """Subtract half-spread + cent-slippage from a planned long exit
    (stop or take-profit). The bot hits the bid, which is lower than
    the planned price."""
    return planned_exit * (1.0 - HALF_SPREAD) - SLIPPAGE_STOP_CENTS

# RTH window (NY-time)
RTH_START_HOUR = 9
RTH_START_MIN = 30
RTH_END_HOUR = 16
RTH_END_MIN = 0

# False-Breakout-Filter Parameter
FBO_LOOKBACK_BARS = 10                       # Window für vol-profile + consolidation
FBO_TOPPING_TAIL_RATIO = 0.5                 # candle als topping-tail klassifizieren
FBO_CONSOLIDATION_RANGE_PCT = 0.5            # last K bars range < X% = stagnant
FBO_MIN_TOPPING_TAILS = 2                    # ≥2 in last 5 bars = filter-hit


@dataclass
class Trade:
    ticker: str
    date: str
    entry_time: str
    entry_price: float
    stop_price: float
    target1_price: float
    target2_price: float
    pole_height: float
    pole_candles: int
    flag_candles: int
    fbo_score: int = 0                       # 0..5 false-breakout indicators triggered
    fbo_breakdown: str = ""
    macd_at_entry: float = 0.0
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_per_share: float = 0.0
    rr_realized: float = 0.0


# ─── Indicator-Berechnung ──────────────────────────────────────────────────
def compute_session_vwap(bars: pd.DataFrame) -> np.ndarray:
    """VWAP per Trading-Day (NY-time), reset bei Session-Open."""
    typical = ((bars["high"] + bars["low"] + bars["close"]) / 3).to_numpy()
    vol = bars["volume"].to_numpy()
    session = bars.index.tz_convert("America/New_York").date
    pv = typical * vol
    df = pd.DataFrame({"pv": pv, "v": vol, "session": session})
    df["cum_pv"] = df.groupby("session")["pv"].cumsum()
    df["cum_v"] = df.groupby("session")["v"].cumsum().replace(0, np.nan)
    return (df["cum_pv"] / df["cum_v"]).to_numpy()


def compute_macd(close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Standard MACD 12/26/9. Returns (macd, signal, histogram)."""
    s = pd.Series(close)
    ema_fast = s.ewm(span=12, adjust=False).mean()
    ema_slow = s.ewm(span=26, adjust=False).mean()
    macd_line = (ema_fast - ema_slow).to_numpy()
    signal = pd.Series(macd_line).ewm(span=9, adjust=False).mean().to_numpy()
    return macd_line, signal, macd_line - signal


def is_rth(timestamp_utc: pd.Timestamp) -> bool:
    """True if timestamp lies inside Regular Trading Hours."""
    ny = timestamp_utc.tz_convert("America/New_York")
    if ny.hour < RTH_START_HOUR or ny.hour >= RTH_END_HOUR:
        return False
    if ny.hour == RTH_START_HOUR and ny.minute < RTH_START_MIN:
        return False
    if ny.hour == RTH_END_HOUR and ny.minute >= RTH_END_MIN:
        return False
    return True


# ─── False-Breakout-Filter (5-Indikatoren-Checkliste) ─────────────────────
def fbo_check(i: int, bars_arrays: dict, lookback: int = FBO_LOOKBACK_BARS) -> tuple[int, str]:
    """Returns (hit_count, breakdown-string). 0..5."""
    o = bars_arrays["open"]
    h = bars_arrays["high"]
    l = bars_arrays["low"]
    c = bars_arrays["close"]
    v = bars_arrays["volume"]
    macd_line = bars_arrays["macd"]
    signal = bars_arrays["signal"]
    topping = bars_arrays["topping_tail"]

    start = max(0, i - lookback)
    hits = 0
    breakdown = []

    # 1. MACD against trade
    if macd_line[i] < signal[i]:
        hits += 1
        breakdown.append("macd_against")

    # 2. Volume-Profile rot-heavy (last lookback bars)
    # FIX: nicht nur "rot > grün" sondern "rot > 1.5x grün" (stricter)
    seg_o = o[start:i]
    seg_c = c[start:i]
    seg_v = v[start:i]
    if len(seg_o) > 0:
        red_mask = seg_c < seg_o
        green_mask = seg_c > seg_o
        red_vol = seg_v[red_mask].sum()
        green_vol = seg_v[green_mask].sum()
        if red_vol > green_vol * 1.5 and red_vol > 0:
            hits += 1
            breakdown.append("red_heavy_vol")

    # 3. History of false breakouts today (≥2 bars mit topping-tail-rejection — strenger)
    # FIX: war ≥1 (zu lose), jetzt ≥2 für signifikante Historie
    if (topping[start:i] > FBO_TOPPING_TAIL_RATIO).sum() >= 2:
        hits += 1
        breakdown.append("history_fbo")

    # 4. Multiple topping-tails in last 5 bars
    last5_start = max(0, i - 5)
    if (topping[last5_start:i] > FBO_TOPPING_TAIL_RATIO).sum() >= FBO_MIN_TOPPING_TAILS:
        hits += 1
        breakdown.append("multi_topping_tails")

    # 5. Zu lange Konsolidierung (last lookback bars range < FBO_CONSOLIDATION_RANGE_PCT)
    if len(seg_c) >= 5:
        rng = (h[start:i].max() - l[start:i].min()) / max(c[i - 1], 1e-9) * 100
        if rng < FBO_CONSOLIDATION_RANGE_PCT:
            hits += 1
            breakdown.append("long_consolidation")

    return hits, "+".join(breakdown) if breakdown else "clean"


# ─── Pattern-Detector (vectorized) ─────────────────────────────────────────
def detect_bull_flag(bars: pd.DataFrame, max_fbo_score: int = 1, debug: bool = False) -> list[Trade]:
    bars = bars.sort_index().copy()
    n = len(bars)
    if n < POLE_CANDLES_MIN + FLAG_CANDLES_MIN + 30:
        return []

    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    l = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    v = bars["volume"].to_numpy()
    green = c > o
    rng = np.maximum(h - l, 1e-9)
    upper_wick = h - np.maximum(c, o)
    topping_tail = upper_wick / rng
    vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()

    # NEW: Session-VWAP statt cumulative
    vwap = compute_session_vwap(bars)
    # NEW: MACD
    macd_line, signal_line, _ = compute_macd(c)

    arrs = {"open": o, "high": h, "low": l, "close": c, "volume": v,
            "topping_tail": topping_tail, "macd": macd_line, "signal": signal_line}

    ticker = str(bars["ticker"].iloc[0])
    times = bars.index

    trades: list[Trade] = []
    min_idx = POLE_CANDLES_MIN + FLAG_CANDLES_MIN

    breakout_candidates = np.where(
        green
        & np.greater(v, vol_sma * BREAKOUT_VOL_FACTOR_MIN,
                     where=~np.isnan(vol_sma),
                     out=np.zeros_like(green, dtype=bool))
    )[0]

    rejected_by_fbo = 0
    rejected_by_macd = 0
    rejected_by_rth = 0

    for i in breakout_candidates:
        if i < min_idx:
            continue
        # NEW: RTH-Filter
        if not is_rth(times[i]):
            rejected_by_rth += 1
            continue

        # NEW: MACD-Cross-Down-Veto
        if macd_line[i] < signal_line[i]:
            rejected_by_macd += 1
            continue

        # NEW: 5-Indikator-FBO-Check
        fbo_score, fbo_str = fbo_check(i, arrs)
        if fbo_score > max_fbo_score:
            rejected_by_fbo += 1
            continue

        matched = False
        for flag_len in range(FLAG_CANDLES_MIN, FLAG_CANDLES_MAX + 1):
            for pole_len in range(POLE_CANDLES_MIN, POLE_CANDLES_MAX + 1):
                ps = i - flag_len - pole_len
                pe = i - flag_len
                if ps < 0:
                    continue
                if not green[ps:pe].all():
                    continue
                pole_start = o[ps]
                pole_end = c[pe - 1]
                if pole_start <= 0:
                    continue
                pole_pct = (pole_end - pole_start) / pole_start * 100.0
                if pole_pct < POLE_MOVE_MIN_PCT:
                    continue
                if topping_tail[ps:pe].max() > POLE_TOPPING_TAIL_MAX_RATIO:
                    continue

                fs = pe
                fe = i
                pole_height = pole_end - pole_start
                if pole_height <= 0:
                    continue
                flag_low = l[fs:fe].min()
                retrace_pct = (pole_end - flag_low) / pole_height * 100.0
                if retrace_pct > FLAG_RETRACE_MAX_PCT:
                    continue
                # NEW: VWAP-Hold mit session-VWAP
                if (c[fs:fe] < vwap[fs:fe]).any():
                    continue

                prev_red_high = h[fs:fe].max()
                if h[i] <= prev_red_high:
                    continue

                # Phase-80: bid/ask spread on entry (pay the ask)
                # and on stop (hit the bid). Replaces the 1¢ slippage
                # which was unrealistically tight for penny stocks.
                entry_price = entry_with_spread(prev_red_high)
                stop_price = exit_with_spread(flag_low)
                if entry_price <= stop_price:
                    continue
                target1 = entry_price + (entry_price - stop_price)
                target2 = entry_price + pole_height

                trades.append(Trade(
                    ticker=ticker,
                    date=str(times[i].date()),
                    entry_time=str(times[i]),
                    entry_price=round(float(entry_price), 4),
                    stop_price=round(float(stop_price), 4),
                    target1_price=round(float(target1), 4),
                    target2_price=round(float(target2), 4),
                    pole_height=round(float(pole_height), 4),
                    pole_candles=int(pole_len),
                    flag_candles=int(flag_len),
                    fbo_score=int(fbo_score),
                    fbo_breakdown=fbo_str,
                    macd_at_entry=round(float(macd_line[i] - signal_line[i]), 6),
                ))
                matched = True
                break
            if matched:
                break

    if debug and ticker:
        log.info("  %s: rejected RTH=%d, MACD=%d, FBO=%d, accepted=%d",
                 ticker, rejected_by_rth, rejected_by_macd, rejected_by_fbo, len(trades))
    return trades


# ─── Exit-Simulation mit MACD-Cross-Down als Hard-Exit ─────────────────────
def simulate_exit(trade: Trade, bars: pd.DataFrame) -> Trade:
    after = bars[bars.index > pd.Timestamp(trade.entry_time)]
    if after.empty:
        trade.exit_reason = "no_bars_after_entry"
        trade.exit_price = trade.entry_price
        return trade

    # Re-compute MACD on full bars for exit-check
    c_arr = bars["close"].to_numpy()
    macd_line, signal_line, _ = compute_macd(c_arr)
    bars_macd = pd.Series(macd_line - signal_line, index=bars.index)

    half_filled = False
    stop = trade.stop_price
    for ts, row in after.iterrows():
        # Stop-Hit (Phase-80: spread on fill = hit the bid)
        if row["low"] <= stop:
            exit_price = exit_with_spread(stop)
            if half_filled:
                trade.exit_price = round(trade.entry_price, 4)
                trade.exit_reason = "stop_hit_after_T1_BE"
                trade.pnl_per_share = round((trade.target1_price - trade.entry_price) * 0.5, 4)
            else:
                trade.exit_price = round(exit_price, 4)
                trade.exit_reason = "stop_hit"
                trade.pnl_per_share = round(exit_price - trade.entry_price, 4)
            trade.exit_time = str(ts)
            trade.rr_realized = round(
                trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
            )
            return trade

        # T1-Hit
        if not half_filled and row["high"] >= trade.target1_price:
            half_filled = True
            stop = trade.entry_price
            continue

        # T2-Hit (Phase-80: hit-bid for both T1 + T2 fills)
        if half_filled and row["high"] >= trade.target2_price:
            t1_fill = exit_with_spread(trade.target1_price)
            t2_fill = exit_with_spread(trade.target2_price)
            r1 = (t1_fill - trade.entry_price) * 0.5
            r2 = (t2_fill - trade.entry_price) * 0.5
            trade.exit_price = round(t2_fill, 4)
            trade.exit_time = str(ts)
            trade.exit_reason = "target2_hit"
            trade.pnl_per_share = round(r1 + r2, 4)
            trade.rr_realized = round(
                trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
            )
            return trade

        # FIX: MACD-Cross-Down nur als Hard-Exit, wenn Position bereits in Profit (T1 reached)
        # UND Close ist über Entry (= still net positive). Sonst wartet man auf Stop.
        # Phase-80: spread on the MACD exit too (market-sell hits bid).
        if half_filled and bars_macd.get(ts, 0) < 0 and row["close"] > trade.entry_price:
            t1_fill = exit_with_spread(trade.target1_price)
            close_fill = exit_with_spread(float(row["close"]))
            r1 = (t1_fill - trade.entry_price) * 0.5
            r_close = (close_fill - trade.entry_price) * 0.5
            trade.exit_price = round(close_fill, 4)
            trade.exit_time = str(ts)
            trade.exit_reason = "macd_cross_down"
            trade.pnl_per_share = round(r1 + r_close, 4)
            trade.rr_realized = round(
                trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
            )
            return trade

    # Phase-80: EOD-exit also hits the bid
    last_close = exit_with_spread(float(after["close"].iloc[-1]))
    if half_filled:
        t1_fill = exit_with_spread(trade.target1_price)
        r1 = (t1_fill - trade.entry_price) * 0.5
        r2 = (last_close - trade.entry_price) * 0.5
        trade.pnl_per_share = round(r1 + r2, 4)
    else:
        trade.pnl_per_share = round(last_close - trade.entry_price, 4)
    trade.exit_price = round(last_close, 4)
    trade.exit_time = str(after.index[-1])
    trade.exit_reason = "eod_exit"
    trade.rr_realized = round(
        trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
    )
    return trade


# ─── Stats ─────────────────────────────────────────────────────────────────
def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = df[df["pnl_per_share"] > 0]
    losses = df[df["pnl_per_share"] <= 0]
    return {
        "n_trades": len(df),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(len(wins) / len(df), 3),
        "avg_winner_per_share": round(wins["pnl_per_share"].mean(), 4) if len(wins) else 0,
        "avg_loser_per_share": round(losses["pnl_per_share"].mean(), 4) if len(losses) else 0,
        "avg_rr_realized": round(df["rr_realized"].mean(), 3),
        "median_rr_realized": round(df["rr_realized"].median(), 3),
        "total_pnl_per_share": round(df["pnl_per_share"].sum(), 4),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
        "fbo_score_dist": df["fbo_score"].value_counts().sort_index().to_dict(),
        "winner_loser_ratio_per_share": round(
            abs(wins["pnl_per_share"].mean() / losses["pnl_per_share"].mean()), 3
        ) if len(losses) and len(wins) else None,
    }


# ─── Main ─────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ticker", type=str, default=None)
    p.add_argument("--debug", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--strict", action="store_true",
                   help="0 false-breakout-hits erlaubt (Cameron-pure)")
    g.add_argument("--moderate", action="store_true",
                   help="≤1 hit erlaubt (default)")
    g.add_argument("--loose", action="store_true",
                   help="≤2 hits erlaubt (Cameron-Schwelle aus YAML)")
    p.add_argument("--out", type=str, default="trades_v2.parquet")
    args = p.parse_args()

    if args.strict:
        max_fbo = 0
    elif args.loose:
        max_fbo = 2
    else:
        max_fbo = 1   # moderate default

    intraday_path = DATA_DIR / "intraday_5m.parquet"
    intraday = pd.read_parquet(intraday_path)
    log.info("Loaded %d rows / %d tickers", len(intraday), intraday["ticker"].nunique())

    tc = next((cn for cn in intraday.columns if "time" in cn.lower() or "date" in cn.lower()), None)
    if tc != "time":
        intraday = intraday.rename(columns={tc: "time"})
    intraday["time"] = pd.to_datetime(intraday["time"], utc=True, errors="coerce")
    intraday = intraday.dropna(subset=["time"])
    intraday["session_date"] = intraday["time"].dt.tz_convert("America/New_York").dt.date

    tickers = intraday["ticker"].unique().tolist()
    if args.ticker:
        tickers = [args.ticker]
    elif args.limit:
        tickers = sorted(tickers)[: args.limit]

    log.info("Filter-Modus: max_fbo_score=%d (strict=0, moderate=1, loose=2)", max_fbo)
    log.info("Backtesting %d tickers", len(tickers))

    all_trades: list[Trade] = []
    for ticker in tickers:
        sub = intraday[intraday["ticker"] == ticker].copy()
        for date, day_bars in sub.groupby("session_date"):
            day_bars = day_bars.set_index("time").sort_index()
            day_bars = day_bars[["open", "high", "low", "close", "volume", "ticker"]]
            day_bars = day_bars.dropna(subset=["open", "high", "low", "close"])
            # FIX: RTH-only — Pre-/Post-Market entfernen vor Pattern-Detection
            day_bars = day_bars[[is_rth(t) for t in day_bars.index]]
            if len(day_bars) < 30:
                continue
            detected = detect_bull_flag(day_bars, max_fbo_score=max_fbo, debug=args.debug)
            for tr in detected:
                tr = simulate_exit(tr, day_bars)
                all_trades.append(tr)

    log.info("Detected %d trades", len(all_trades))
    if all_trades:
        out_df = pd.DataFrame([asdict(t) for t in all_trades])
        out_df.to_parquet(DATA_DIR / args.out)

    stats = summarize(all_trades)
    print("\n=== STATS V2 (max_fbo=%d) ===" % max_fbo)
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\n=== CAMERON-BENCHMARK ===")
    print("  Lifetime: 68% win-rate, 11¢ avg-winner, 8¢ avg-loser, R/R ≥ 2:1")
    print("  Min-Profitable bei 2:1 R/R: 33% Win-Rate")


if __name__ == "__main__":
    main()
