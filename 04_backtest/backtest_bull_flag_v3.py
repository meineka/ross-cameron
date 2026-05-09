"""
backtest_bull_flag_v3.py — Cameron-Workflow-konform.

Hauptverbesserung gegen v2:
  TOP-N-PRO-TAG-FILTER: Cameron tradet nicht alle Stocks die qualifizieren,
  sondern nur die TOP 1-3 stärksten pro Tag. Wir ranken Candidates innerhalb
  jedes Datums nach Composite-Score (RVOL × intraday_pct) und behalten nur
  die Top-N.

Zusätzlich:
  - Konfigurierbarer Pole-Min-% (Default 5, Test 7)
  - Optionaler Catalyst-Required-Filter (nutzt EDGAR-8K-Tagging)
  - Stats nach Setup (per-day-rank Auswertung)

Usage:
  python backtest_bull_flag_v3.py --top-n 3                 # Cameron-Style
  python backtest_bull_flag_v3.py --top-n 3 --pole-pct 7    # strikteres Pole
  python backtest_bull_flag_v3.py --top-n 3 --require-catalyst
  python backtest_bull_flag_v3.py                           # ohne top-n (= v2)
"""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import argparse, logging
from dataclasses import dataclass, asdict
from pathlib import Path
import numpy as np, pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent / "data_pilot"

# ── Defaults (konfigurierbar via CLI) ──────────────────────────────────────
POLE_CANDLES_MIN, POLE_CANDLES_MAX = 3, 7
POLE_TOPPING_TAIL_MAX_RATIO = 0.4
FLAG_CANDLES_MIN, FLAG_CANDLES_MAX = 1, 3
FLAG_RETRACE_MAX_PCT = 50.0
BREAKOUT_VOL_FACTOR_MIN = 1.5
SLIPPAGE_CENTS = 0.01
RTH_START_H, RTH_START_M, RTH_END_H = 9, 30, 16

FBO_LOOKBACK_BARS = 10
FBO_TOPPING_TAIL_RATIO = 0.5
FBO_CONSOLIDATION_RANGE_PCT = 0.5
FBO_MIN_TOPPING_TAILS = 2


@dataclass
class Trade:
    ticker: str; date: str; entry_time: str
    entry_price: float; stop_price: float; target1_price: float; target2_price: float
    pole_height: float; pole_candles: int; flag_candles: int
    fbo_score: int = 0; fbo_breakdown: str = ""; macd_at_entry: float = 0.0
    rank_in_day: int = -1; day_score: float = 0.0
    has_8k: bool = False
    exit_time: str = ""; exit_price: float = 0.0; exit_reason: str = ""
    pnl_per_share: float = 0.0; rr_realized: float = 0.0


# ─── Indicators ─────────────────────────────────────────────────────────────
def session_vwap(bars):
    typ = ((bars["high"] + bars["low"] + bars["close"]) / 3).to_numpy()
    vol = bars["volume"].to_numpy()
    sess = bars.index.tz_convert("America/New_York").date
    df = pd.DataFrame({"pv": typ * vol, "v": vol, "session": sess})
    df["cum_pv"] = df.groupby("session")["pv"].cumsum()
    df["cum_v"] = df.groupby("session")["v"].cumsum().replace(0, np.nan)
    return (df["cum_pv"] / df["cum_v"]).to_numpy()


def macd(close):
    s = pd.Series(close)
    macd_l = (s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()).to_numpy()
    sig = pd.Series(macd_l).ewm(span=9, adjust=False).mean().to_numpy()
    return macd_l, sig


def is_rth(ts):
    ny = ts.tz_convert("America/New_York")
    if ny.hour < RTH_START_H or ny.hour >= RTH_END_H: return False
    if ny.hour == RTH_START_H and ny.minute < RTH_START_M: return False
    return True


# ─── False-Breakout-Filter ──────────────────────────────────────────────────
def fbo_check(i, arrs, lookback=FBO_LOOKBACK_BARS):
    o, h, l, c, v = arrs["open"], arrs["high"], arrs["low"], arrs["close"], arrs["volume"]
    macd_l, sig, top = arrs["macd"], arrs["signal"], arrs["topping_tail"]
    start = max(0, i - lookback)
    hits, breakdown = 0, []
    if macd_l[i] < sig[i]:
        hits += 1; breakdown.append("macd_against")
    seg_v = v[start:i]; seg_o = o[start:i]; seg_c = c[start:i]
    if len(seg_o):
        red_vol = seg_v[seg_c < seg_o].sum()
        green_vol = seg_v[seg_c > seg_o].sum()
        if red_vol > green_vol * 1.5 and red_vol > 0:
            hits += 1; breakdown.append("red_heavy_vol")
    if (top[start:i] > FBO_TOPPING_TAIL_RATIO).sum() >= 2:
        hits += 1; breakdown.append("history_fbo")
    last5 = max(0, i - 5)
    if (top[last5:i] > FBO_TOPPING_TAIL_RATIO).sum() >= FBO_MIN_TOPPING_TAILS:
        hits += 1; breakdown.append("multi_topping_tails")
    if len(seg_c) >= 5:
        rng = (h[start:i].max() - l[start:i].min()) / max(c[i - 1], 1e-9) * 100
        if rng < FBO_CONSOLIDATION_RANGE_PCT:
            hits += 1; breakdown.append("long_consolidation")
    return hits, "+".join(breakdown) if breakdown else "clean"


# ─── Pattern-Detection ──────────────────────────────────────────────────────
def detect(bars, max_fbo, pole_pct_min, ticker_meta):
    bars = bars.sort_index()
    n = len(bars)
    if n < POLE_CANDLES_MIN + FLAG_CANDLES_MIN + 30: return []
    o = bars["open"].to_numpy(); h = bars["high"].to_numpy(); l = bars["low"].to_numpy()
    c = bars["close"].to_numpy(); v = bars["volume"].to_numpy()
    green = c > o; rng = np.maximum(h - l, 1e-9)
    upper_wick = h - np.maximum(c, o)
    topping = upper_wick / rng
    vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
    vw = session_vwap(bars)
    macd_l, sig = macd(c)
    arrs = {"open": o, "high": h, "low": l, "close": c, "volume": v,
            "topping_tail": topping, "macd": macd_l, "signal": sig}
    times = bars.index
    ticker = str(bars["ticker"].iloc[0])
    trades = []
    min_idx = POLE_CANDLES_MIN + FLAG_CANDLES_MIN
    cands = np.where(green & np.greater(v, vol_sma * BREAKOUT_VOL_FACTOR_MIN,
        where=~np.isnan(vol_sma), out=np.zeros_like(green, dtype=bool)))[0]

    for i in cands:
        if i < min_idx or not is_rth(times[i]): continue
        if macd_l[i] < sig[i]: continue
        fbo, fbo_str = fbo_check(i, arrs)
        if fbo > max_fbo: continue
        matched = False
        for fl in range(FLAG_CANDLES_MIN, FLAG_CANDLES_MAX + 1):
            for pl in range(POLE_CANDLES_MIN, POLE_CANDLES_MAX + 1):
                ps = i - fl - pl; pe = i - fl
                if ps < 0 or not green[ps:pe].all(): continue
                p_start = o[ps]; p_end = c[pe-1]
                if p_start <= 0: continue
                p_pct = (p_end - p_start) / p_start * 100
                if p_pct < pole_pct_min: continue
                if topping[ps:pe].max() > POLE_TOPPING_TAIL_MAX_RATIO: continue
                fs = pe; fe = i
                p_h = p_end - p_start
                if p_h <= 0: continue
                fl_low = l[fs:fe].min()
                if (p_end - fl_low) / p_h * 100 > FLAG_RETRACE_MAX_PCT: continue
                if (c[fs:fe] < vw[fs:fe]).any(): continue
                prh = h[fs:fe].max()
                if h[i] <= prh: continue
                ep = prh + SLIPPAGE_CENTS
                sp = fl_low - SLIPPAGE_CENTS
                if ep <= sp: continue
                trades.append(Trade(
                    ticker=ticker, date=str(times[i].date()), entry_time=str(times[i]),
                    entry_price=round(float(ep), 4), stop_price=round(float(sp), 4),
                    target1_price=round(float(ep + (ep - sp)), 4),
                    target2_price=round(float(ep + p_h), 4),
                    pole_height=round(float(p_h), 4),
                    pole_candles=int(pl), flag_candles=int(fl),
                    fbo_score=int(fbo), fbo_breakdown=fbo_str,
                    macd_at_entry=round(float(macd_l[i] - sig[i]), 6),
                    rank_in_day=ticker_meta.get("rank", -1),
                    day_score=ticker_meta.get("score", 0.0),
                    has_8k=ticker_meta.get("has_8k", False),
                ))
                matched = True; break
            if matched: break
    return trades


# ─── Exit-Sim ───────────────────────────────────────────────────────────────
def simulate_exit(t, bars):
    after = bars[bars.index > pd.Timestamp(t.entry_time)]
    if after.empty:
        t.exit_reason="no_bars_after_entry"; t.exit_price=t.entry_price; return t
    macd_l, sig = macd(bars["close"].to_numpy())
    bars_md = pd.Series(macd_l - sig, index=bars.index)
    half_filled = False
    stop = t.stop_price
    for ts, row in after.iterrows():
        if row["low"] <= stop:
            ep = stop - SLIPPAGE_CENTS
            if half_filled:
                t.exit_price = round(t.entry_price, 4)
                t.exit_reason = "stop_hit_after_T1_BE"
                t.pnl_per_share = round((t.target1_price - t.entry_price) * 0.5, 4)
            else:
                t.exit_price = round(ep, 4)
                t.exit_reason = "stop_hit"
                t.pnl_per_share = round(ep - t.entry_price, 4)
            t.exit_time = str(ts)
            t.rr_realized = round(t.pnl_per_share / max(t.entry_price - t.stop_price, 1e-9), 3)
            return t
        if not half_filled and row["high"] >= t.target1_price:
            half_filled = True; stop = t.entry_price; continue
        if half_filled and row["high"] >= t.target2_price:
            r1 = (t.target1_price - t.entry_price) * 0.5
            r2 = (t.target2_price - t.entry_price) * 0.5
            t.exit_price = t.target2_price; t.exit_time = str(ts)
            t.exit_reason = "target2_hit"
            t.pnl_per_share = round(r1 + r2, 4)
            t.rr_realized = round(t.pnl_per_share / max(t.entry_price - t.stop_price, 1e-9), 3)
            return t
        if half_filled and bars_md.get(ts, 0) < 0 and row["close"] > t.entry_price:
            r1 = (t.target1_price - t.entry_price) * 0.5
            rc = (row["close"] - t.entry_price) * 0.5
            t.exit_price = round(float(row["close"]), 4); t.exit_time = str(ts)
            t.exit_reason = "macd_cross_down"
            t.pnl_per_share = round(r1 + rc, 4)
            t.rr_realized = round(t.pnl_per_share / max(t.entry_price - t.stop_price, 1e-9), 3)
            return t
    last = after["close"].iloc[-1]
    if half_filled:
        r1 = (t.target1_price - t.entry_price) * 0.5
        r2 = (last - t.entry_price) * 0.5
        t.pnl_per_share = round(r1 + r2, 4)
    else:
        t.pnl_per_share = round(last - t.entry_price, 4)
    t.exit_price = round(last, 4); t.exit_time = str(after.index[-1])
    t.exit_reason = "eod_exit"
    t.rr_realized = round(t.pnl_per_share / max(t.entry_price - t.stop_price, 1e-9), 3)
    return t


# ─── Stats ──────────────────────────────────────────────────────────────────
def summarize(trades):
    if not trades: return {"n_trades": 0}
    df = pd.DataFrame([asdict(t) for t in trades])
    wins = df[df["pnl_per_share"] > 0]; losses = df[df["pnl_per_share"] <= 0]
    out = {
        "n_trades": len(df), "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": round(len(wins) / len(df), 3),
        "avg_winner": round(wins["pnl_per_share"].mean(), 4) if len(wins) else 0,
        "avg_loser": round(losses["pnl_per_share"].mean(), 4) if len(losses) else 0,
        "avg_rr": round(df["rr_realized"].mean(), 3),
        "median_rr": round(df["rr_realized"].median(), 3),
        "total_pnl_per_share": round(df["pnl_per_share"].sum(), 4),
        "exit_reasons": df["exit_reason"].value_counts().to_dict(),
        "winner_loser_ratio": round(abs(wins["pnl_per_share"].mean()/losses["pnl_per_share"].mean()), 3) if len(losses) and len(wins) else None,
    }
    if "rank_in_day" in df.columns and (df["rank_in_day"] >= 0).any():
        rnk = df.groupby("rank_in_day")["pnl_per_share"].agg(["count", "mean", lambda s: (s>0).mean()])
        rnk.columns = ["n", "avg_pnl", "win_rate"]
        out["per_rank"] = rnk.to_dict()
    if df["has_8k"].any():
        out["catalyst_split"] = df.groupby("has_8k").agg(
            n=("pnl_per_share", "count"),
            wr=("pnl_per_share", lambda s: round((s > 0).mean(), 3)),
            avg=("pnl_per_share", "mean"),
        ).to_dict()
    return out


# ─── Filter: Top-N-pro-Tag-Ranking ──────────────────────────────────────────
def build_top_n_filter(top_n, require_catalyst):
    """Returns dict {(ticker, date) -> {rank, score, has_8k}} for top-N picks."""
    cands = pd.read_parquet(DATA_DIR / "candidates.parquet")
    cands["date_only"] = pd.to_datetime(cands["date"]).dt.date
    cands["score"] = cands["rvol_proxy"] * cands["intraday_pct"]
    cands["rank"] = cands.groupby("date_only")["score"].rank(ascending=False, method="dense")
    cands_sorted = cands.sort_values(["date_only", "rank"])
    if top_n is not None:
        cands_sorted = cands_sorted[cands_sorted["rank"] <= top_n]

    catalyst_map = {}
    full = DATA_DIR / "candidates_with_catalyst_full.parquet"
    sample = DATA_DIR / "candidates_with_catalyst_sample.parquet"
    cat_path = full if full.exists() else sample
    if cat_path.exists():
        cat = pd.read_parquet(cat_path)
        cat["date_only"] = pd.to_datetime(cat["date"]).dt.date
        for r in cat.itertuples():
            catalyst_map[(r.ticker, r.date_only)] = bool(getattr(r, "has_8k", False) or False)

    out = {}
    for r in cands_sorted.itertuples():
        has_8k = catalyst_map.get((r.ticker, r.date_only), False)
        if require_catalyst and not has_8k: continue
        out[(r.ticker, r.date_only)] = {
            "rank": int(r.rank), "score": float(r.score), "has_8k": has_8k
        }
    return out


# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top-n", type=int, default=None,
                   help="Top-N strongest stocks per day (Cameron-Style: 3)")
    p.add_argument("--pole-pct", type=float, default=5.0,
                   help="Min Pole-% (default 5.0; strict 7.0)")
    p.add_argument("--require-catalyst", action="store_true",
                   help="Nur Trades wenn 8-K-Filing nahe Datum")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ticker", type=str, default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--strict", action="store_true")
    g.add_argument("--moderate", action="store_true")
    g.add_argument("--loose", action="store_true")
    p.add_argument("--out", type=str, default="trades_v3.parquet")
    args = p.parse_args()
    max_fbo = 0 if args.strict else (2 if args.loose else 1)

    top_filter = build_top_n_filter(args.top_n, args.require_catalyst)
    log.info("Filter: top_n=%s, require_catalyst=%s, pole_pct=%.1f, max_fbo=%d => %d allowed (ticker, date)",
             args.top_n, args.require_catalyst, args.pole_pct, max_fbo, len(top_filter))

    intraday = pd.read_parquet(DATA_DIR / "intraday_5m.parquet")
    tc = next((c for c in intraday.columns if "time" in c.lower() or "date" in c.lower()), None)
    if tc != "time": intraday = intraday.rename(columns={tc: "time"})
    intraday["time"] = pd.to_datetime(intraday["time"], utc=True, errors="coerce")
    intraday = intraday.dropna(subset=["time"])
    intraday["session_date"] = intraday["time"].dt.tz_convert("America/New_York").dt.date

    tickers = intraday["ticker"].unique().tolist()
    if args.ticker: tickers = [args.ticker]
    elif args.limit: tickers = sorted(tickers)[:args.limit]
    log.info("Loaded %d intraday rows / %d tickers, processing %d", len(intraday), intraday["ticker"].nunique(), len(tickers))

    all_trades = []
    skipped_not_in_top = 0
    for ticker in tickers:
        sub = intraday[intraday["ticker"] == ticker].copy()
        for date, day_bars in sub.groupby("session_date"):
            meta = top_filter.get((ticker, date))
            if meta is None:
                skipped_not_in_top += 1
                continue
            day_bars = day_bars.set_index("time").sort_index()
            day_bars = day_bars[["open","high","low","close","volume","ticker"]]
            day_bars = day_bars.dropna(subset=["open","high","low","close"])
            day_bars = day_bars[[is_rth(t) for t in day_bars.index]]
            if len(day_bars) < 30: continue
            for tr in detect(day_bars, max_fbo, args.pole_pct, meta):
                all_trades.append(simulate_exit(tr, day_bars))

    log.info("Detected %d trades (%d ticker-days skipped: outside top-N)", len(all_trades), skipped_not_in_top)
    if all_trades:
        pd.DataFrame([asdict(t) for t in all_trades]).to_parquet(DATA_DIR / args.out)

    print("\n=== STATS V3 ===")
    print(f"  config: top-n={args.top_n}, pole={args.pole_pct}%, max_fbo={max_fbo}, catalyst={args.require_catalyst}")
    for k, v in summarize(all_trades).items():
        print(f"  {k}: {v}")
    print("\n=== CAMERON ===")
    print("  Lifetime 68% WR · 11¢ avg-winner · 8¢ avg-loser · R/R≥2:1")


if __name__ == "__main__":
    main()
