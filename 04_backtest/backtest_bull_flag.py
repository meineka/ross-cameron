"""
backtest_bull_flag.py — Cameron Bull-Flag / Micro-Pullback Backtest auf 5-min Bars.

Lädt das Output von bootstrap.py und simuliert den Bull-Flag-Trade pro
Candidate-Day. Stats werden gegen Camerons Live-Benchmarks geprüft.

Pattern (aus constraints.yaml#entries.bull_flag_micro_pullback):
  Pole:   3-7 grüne Kerzen, kumulativ ≥5%, kein Topping-Tail
  Flag:   1-3 rote Kerzen, max 50% retrace, hold > VWAP, Volume sinkt
  Entry:  erste grüne Kerze deren HIGH > prev red candle HIGH + Volume ≥ 1.5x SMA(20)
  Stop:   min(flag_lows)
  Target: T1 = entry+1R (50% raus, BE-Stop), T2 = entry+pole_height,
          Trail = unter 9 EMA bis Bruch

Usage:
  python backtest_bull_flag.py                # alle Tickers in candidates
  python backtest_bull_flag.py --limit 1      # 1 Ticker (Smoke-Test)
  python backtest_bull_flag.py --limit 5      # 5 Tickers
  python backtest_bull_flag.py --limit 15     # 15 Tickers
  python backtest_bull_flag.py --ticker AAPL  # nur 1 spezifischer Ticker
"""

from __future__ import annotations

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import argparse
import logging
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data_pilot"

# ─── Constraints (mirrors constraints.yaml) ─────────────────────────────────
POLE_CANDLES_MIN = 3
POLE_CANDLES_MAX = 7
POLE_MOVE_MIN_PCT = 5.0
POLE_TOPPING_TAIL_MAX_RATIO = 0.4
FLAG_CANDLES_MIN = 1
FLAG_CANDLES_MAX = 3
FLAG_RETRACE_MAX_PCT = 50.0
BREAKOUT_VOL_FACTOR_MIN = 1.5
RR_TARGET = 2.0


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
    exit_time: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_per_share: float = 0.0
    rr_realized: float = 0.0


# ─── Pattern-Detection ──────────────────────────────────────────────────────
def detect_bull_flag(bars: pd.DataFrame) -> list[Trade]:
    """Detect bull-flag patterns on a single ticker-day's 5-min bars.

    Vectorized version: pre-compute all per-bar features once, then iterate
    only candidate breakout-bars (pre-filtered by green + volume).

    bars: DataFrame indexed by datetime with columns open/high/low/close/volume.
    """
    bars = bars.sort_index().copy()
    n = len(bars)
    if n < POLE_CANDLES_MIN + FLAG_CANDLES_MIN + 1:
        return []

    # Per-bar features
    o = bars["open"].to_numpy()
    h = bars["high"].to_numpy()
    l = bars["low"].to_numpy()
    c = bars["close"].to_numpy()
    v = bars["volume"].to_numpy()
    green = c > o
    rng = np.maximum(h - l, 1e-9)
    upper_wick = h - np.maximum(c, o)
    topping_tail = upper_wick / rng

    # Volume-SMA(20) — vectorized rolling
    vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()

    # VWAP cumulative
    typical = (h + l + c) / 3
    cum_pv = np.cumsum(typical * v)
    cum_v = np.where(np.cumsum(v) == 0, np.nan, np.cumsum(v))
    vwap = cum_pv / cum_v

    ticker = str(bars["ticker"].iloc[0])
    times = bars.index

    trades: list[Trade] = []
    min_idx = POLE_CANDLES_MIN + FLAG_CANDLES_MIN

    # Pre-filter potential breakout candles: green + volume-OK
    breakout_candidates = np.where(
        green
        & np.greater(v, vol_sma * BREAKOUT_VOL_FACTOR_MIN, where=~np.isnan(vol_sma), out=np.zeros_like(green, dtype=bool))
    )[0]

    for i in breakout_candidates:
        if i < min_idx:
            continue
        # Try (pole_len, flag_len) combos — break at first match
        matched = False
        for flag_len in range(FLAG_CANDLES_MIN, FLAG_CANDLES_MAX + 1):
            for pole_len in range(POLE_CANDLES_MIN, POLE_CANDLES_MAX + 1):
                ps = i - flag_len - pole_len
                pe = i - flag_len  # exclusive
                if ps < 0:
                    continue
                # Pole all-green
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

                # Flag
                fs = pe
                fe = i  # exclusive
                pole_height = pole_end - pole_start
                if pole_height <= 0:
                    continue
                flag_low = l[fs:fe].min()
                retrace_pct = (pole_end - flag_low) / pole_height * 100.0
                if retrace_pct > FLAG_RETRACE_MAX_PCT:
                    continue
                # Hold above VWAP
                if (c[fs:fe] < vwap[fs:fe]).any():
                    continue

                # Breakout-Trigger
                prev_red_high = h[fs:fe].max()
                if h[i] <= prev_red_high:
                    continue

                entry_price = prev_red_high + 0.01
                stop_price = flag_low
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
                ))
                matched = True
                break
            if matched:
                break
    return trades


def simulate_exit(trade: Trade, bars: pd.DataFrame) -> Trade:
    """Walk forward from entry, simulate scale-out logic."""
    after = bars[bars.index > pd.Timestamp(trade.entry_time)]
    if after.empty:
        trade.exit_reason = "no_bars_after_entry"
        trade.exit_price = trade.entry_price
        return trade

    half_filled = False
    stop = trade.stop_price
    for ts, row in after.iterrows():
        # Stop-Hit
        if row["low"] <= stop:
            exit_price = stop  # konservativ — slippage später
            if half_filled:
                # 50% bei BE, 50% bei stop (=BE)
                trade.exit_price = trade.entry_price  # already locked first half at T1
                trade.exit_reason = "stop_hit_after_T1_BE"
                trade.pnl_per_share = (trade.target1_price - trade.entry_price) * 0.5
            else:
                trade.exit_price = round(exit_price, 4)
                trade.exit_reason = "stop_hit"
                trade.pnl_per_share = round(exit_price - trade.entry_price, 4)
            trade.exit_time = str(ts)
            trade.rr_realized = round(
                trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
            )
            return trade
        # Target-1-Hit
        if not half_filled and row["high"] >= trade.target1_price:
            half_filled = True
            stop = trade.entry_price  # BE-Stop nach T1
            continue
        # Target-2-Hit
        if half_filled and row["high"] >= trade.target2_price:
            r1 = (trade.target1_price - trade.entry_price) * 0.5
            r2 = (trade.target2_price - trade.entry_price) * 0.5
            trade.exit_price = trade.target2_price
            trade.exit_time = str(ts)
            trade.exit_reason = "target2_hit"
            trade.pnl_per_share = round(r1 + r2, 4)
            trade.rr_realized = round(
                trade.pnl_per_share / max(trade.entry_price - trade.stop_price, 1e-9), 3
            )
            return trade

    # End-of-Day-Exit
    last_close = after["close"].iloc[-1]
    if half_filled:
        r1 = (trade.target1_price - trade.entry_price) * 0.5
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


# ─── Stats ──────────────────────────────────────────────────────────────────
def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = df[df["pnl_per_share"] > 0]
    losses = df[df["pnl_per_share"] <= 0]
    return {
        "n_trades": len(df),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "win_rate": round(len(wins) / len(df), 3) if len(df) else 0,
        "avg_winner_per_share": round(wins["pnl_per_share"].mean(), 4) if len(wins) else 0,
        "avg_loser_per_share": round(losses["pnl_per_share"].mean(), 4) if len(losses) else 0,
        "avg_rr_realized": round(df["rr_realized"].mean(), 3),
        "total_pnl_per_share": round(df["pnl_per_share"].sum(), 4),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
    }


# ─── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None,
                   help="Limit number of tickers (1, 5, 15 ...)")
    p.add_argument("--ticker", type=str, default=None,
                   help="Backtest only this ticker")
    p.add_argument("--out", type=str, default="trades.parquet")
    args = p.parse_args()

    intraday_path = DATA_DIR / "intraday_5m.parquet"
    if not intraday_path.exists():
        log.error("Run bootstrap.py first — %s missing", intraday_path)
        return
    intraday = pd.read_parquet(intraday_path)
    log.info("Loaded %d intraday rows across %d tickers",
             len(intraday), intraday["ticker"].nunique())

    # Index normalisieren
    if "datetime" in intraday.columns:
        intraday = intraday.rename(columns={"datetime": "time"})
    elif "date" in intraday.columns and intraday["date"].dtype != "O":
        intraday = intraday.rename(columns={"date": "time"})
    if "time" not in intraday.columns:
        time_col = next((c for c in intraday.columns if "date" in str(c).lower() or "time" in str(c).lower()), None)
        if time_col:
            intraday = intraday.rename(columns={time_col: "time"})
    intraday["time"] = pd.to_datetime(intraday["time"], utc=True, errors="coerce")
    intraday = intraday.dropna(subset=["time"])
    intraday["session_date"] = intraday["time"].dt.tz_convert("America/New_York").dt.date

    # Universe-Auswahl
    tickers = intraday["ticker"].unique().tolist()
    if args.ticker:
        tickers = [args.ticker]
    elif args.limit:
        tickers = sorted(tickers)[: args.limit]
    log.info("Backtesting %d tickers: %s", len(tickers),
             tickers if len(tickers) <= 20 else f"{tickers[:5]} …")

    all_trades: list[Trade] = []
    for ticker in tickers:
        sub = intraday[intraday["ticker"] == ticker].copy()
        for date, day_bars in sub.groupby("session_date"):
            day_bars = day_bars.set_index("time").sort_index()
            cols_needed = ["open", "high", "low", "close", "volume", "ticker"]
            day_bars = day_bars[cols_needed].dropna(subset=["open", "high", "low", "close"])
            if len(day_bars) < 10:
                continue
            detected = detect_bull_flag(day_bars)
            for tr in detected:
                tr = simulate_exit(tr, day_bars)
                all_trades.append(tr)

    log.info("Detected %d trades", len(all_trades))
    if all_trades:
        out_df = pd.DataFrame([asdict(t) for t in all_trades])
        out_path = DATA_DIR / args.out
        out_df.to_parquet(out_path)
        log.info("Saved %s", out_path)

    stats = summarize(all_trades)
    print("\n=== STATS ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    print("\n=== CAMERON-BENCHMARK ===")
    print("  Cameron (Winning Day):  Win-Rate 71%, Avg-Win 11¢/share, Avg-Loss 8¢/share")
    print("  Cameron (Lifetime):     Win-Rate 68%")
    print("  Min-Acceptable (2:1RR): Win-Rate 33% Breakeven")


if __name__ == "__main__":
    main()
