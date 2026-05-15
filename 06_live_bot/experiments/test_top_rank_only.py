"""Test ob nur Top-N-ranked Symbols traden besser ist als alle 10.

Cameron-Quote: "I focus on the 2-3 best setups of the day, not all 10."
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def run_with_rank_cap(name, max_rank, dates):
    """Replay with cap: only ts.rank <= max_rank can trade."""
    total_pnl = 0
    total_trades = 0
    daily = []
    wins = losses = spirals = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
        try:
            rb.run(d.isoformat())
            # Re-run logic gets complex — easier: filter at TS creation
        except Exception:
            continue
        # Naive: after run, count trades that came from top-N tickers
        # ReplayBot already keeps tickers; just count by ts.rank from initial
        # This requires hooking into _manage; let me do a cleaner approach via
        # monkey-patching detect_bull_flag to reject non-top-N
        pnl = round(rb.day.realized_pnl, 2)
        total_pnl += pnl
        total_trades += rb.day.trades_completed_today
        daily.append(pnl)
        if rb.day.spiral_locked: spirals += 1
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "max_rank": max_rank, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals, "sharpe": sharpe}


# Cleaner: hook detect_bull_flag with rank-aware filter
def make_rank_filtered_detect(max_rank, get_ticker_by_bars):
    _orig = bot_mod.detect_bull_flag

    def filtered(bars):
        ok, params = _orig(bars)
        if not ok:
            return ok, params
        # Get ticker context — we need to know which symbol's bars these are
        # ReplayBot._manage iterates over tickers; the bars belong to a specific ts
        # We can't easily access that from detect_bull_flag directly.
        # Workaround: ReplayBot._manage stores ts ranking. Easier to patch
        # _manage itself.
        return ok, params
    return filtered


# Best approach: patch ReplayBot to skip entries when rank > max_rank
def run_with_rank_filter(name, max_rank, dates):
    _orig_run = bot_mod.ReplayBot.run

    def patched_run(self, target_date):
        # Run original logic, then post-filter trades by rank
        return _orig_run(self, target_date)

    # Easier: re-create the streaming loop with rank check
    # ReplayBot.run reads top-N watchlist + streams bars. We need to filter
    # entry submission by rank.
    # Since rank is set at TS creation, we can just SKIP tickers with rank > N
    # by removing them BEFORE the stream loop.

    total_pnl = 0
    total_trades = 0
    daily = []
    wins = losses = spirals = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
        # Hook: after watchlist load, drop tickers with rank > max_rank
        _orig_run_inner = rb.run
        def filtered_run(td, _self=rb, _orig=_orig_run_inner):
            # Run setup phase by overriding TS creation
            # Easier: do post-creation drop. Need to look at run() flow.
            return _orig(td)
        # Monkey-patch: drop higher-ranked tickers AFTER setup
        rb._orig_run = _orig_run_inner
        try:
            _orig_run_inner(d.isoformat())
            # Now manually drop high-rank tickers and re-replay won't work
            # Instead, simpler: post-hoc analyze
        except Exception:
            continue
        # Filter PnL/trades by rank — count only trades that came from top-N
        # In ReplayBot, tickers dict has rank info. Trades aren't separately tracked
        # in DayState. We need to instrument the submit_buy hook.
        pnl = round(rb.day.realized_pnl, 2)
        total_pnl += pnl
        total_trades += rb.day.trades_completed_today
        daily.append(pnl)
        if rb.day.spiral_locked: spirals += 1
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2), "spirals": spirals,
            "sharpe": sharpe}


# CLEAN APPROACH: instrument ReplayBot directly — overload run() to drop tickers
# with rank > N before the stream loop starts.

def run_top_n(name, max_rank, dates):
    """Inject TS filter into ReplayBot via class-level monkey-patch."""
    total_pnl = 0; total_trades = 0; daily = []; wins = losses = spirals = 0

    _orig_run = bot_mod.ReplayBot.run

    def patched_run(self, target_date):
        # Call original but inject filter at first opportunity
        bars_path, cands_path = bot_mod.find_pilot_data_paths()
        if bars_path is None: return
        bars = pd.read_parquet(bars_path)
        cands = pd.read_parquet(cands_path)
        tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
        bars[tc] = pd.to_datetime(bars[tc], utc=True)
        bars["session_date"] = bars[tc].dt.tz_convert("America/New_York").dt.date
        target = pd.to_datetime(target_date).date()
        day_bars = bars[bars["session_date"] == target].sort_values(tc)
        if day_bars.empty: return
        cands["date"] = pd.to_datetime(cands["date"]).dt.date
        day_cands = cands[cands["date"] == target].copy()
        if day_cands.empty: return
        day_cands["score"] = day_cands["rvol_proxy"] * day_cands["intraday_pct"]
        top = day_cands.sort_values("score", ascending=False).head(bot_mod.TOP_N)
        # FILTER: only keep top max_rank
        top_filtered = top.head(max_rank)
        for rank, row in enumerate(top_filtered.itertuples()):
            self.tickers[row.ticker] = bot_mod.TickerState(
                symbol=row.ticker, rank=rank+1, score=float(row.score))
        # Stream bars
        relevant = day_bars[day_bars["ticker"].isin(self.tickers.keys())].sort_values(tc)
        for _, b in relevant.iterrows():
            sym = b["ticker"]
            ts = self.tickers[sym]
            bar = {"open": b["open"], "high": b["high"], "low": b["low"],
                   "close": b["close"], "volume": b["volume"], "timestamp": b[tc]}
            ts.bars.append(bar)
            ny_t = b[tc].tz_convert("America/New_York").time()
            if ts.in_position:
                self._manage(ts, bar, ny_t); continue
            ok, reason = bot_mod.can_enter_new(self.day, ny_t)
            if not ok: continue
            signal, params = bot_mod.detect_bull_flag(list(ts.bars))
            if not signal: continue
            ts.pullback_count_today += 1
            if ts.pullback_count_today >= 3: continue
            shares = bot_mod.compute_position_size(
                params["entry_price"], params["stop_price"], self.equity, self.day)
            if shares < 1: continue
            self.submit_buy(sym, shares, params["entry_price"])
            ts.in_position = True
            ts.entry_price = params["entry_price"]; ts.stop_price = params["stop_price"]
            ts.target1_price = params["target1"]; ts.target2_price = params["target2"]
            ts.shares = shares; ts.initial_shares = shares
            ts.t1_shares_sold = 0; ts.half_filled = False

    bot_mod.ReplayBot.run = patched_run
    try:
        for d in dates:
            rb = bot_mod.ReplayBot()
            try:
                rb.run(d.isoformat())
            except Exception:
                continue
            pnl = round(rb.day.realized_pnl, 2)
            total_pnl += pnl
            total_trades += rb.day.trades_completed_today
            daily.append(pnl)
            if rb.day.spiral_locked: spirals += 1
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
    finally:
        bot_mod.ReplayBot.run = _orig_run

    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "trades": total_trades, "pnl": round(total_pnl, 2),
            "win_rate": wr, "max_dd": round(mdd, 2), "spirals": spirals,
            "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nTop-N-Rank Filter ({len(dates)} days)\n")
configs = [
    ("BASELINE all 10",   10),
    ("Top 7",              7),
    ("Top 5",              5),
    ("Top 3",              3),
    ("Top 2",              2),
    ("Top 1 only",         1),
]
results = []
for name, n in configs:
    r = run_top_n(name, n, dates)
    results.append(r)
    print(f"{name:<20} N={n:<2}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'BASELINE' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
