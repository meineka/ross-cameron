"""Test pullback-count-limit sweep.

Cameron-Praxis: nach 2 failed pullbacks = stock dead.
Aktueller Bot: >=3 (toleriert 2 successful = pullback #1 und #2).
Variants: limit 2 (cameron-strict), 3 (current), 4 (looser).
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)


def run_with_limit(name, limit, dates):
    """Monkey-patch ReplayBot.run to use custom pullback-count cap."""
    _orig_run = bot_mod.ReplayBot.run

    def patched_run(self, target_date):
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
        for rank, row in enumerate(top.itertuples()):
            self.tickers[row.ticker] = bot_mod.TickerState(
                symbol=row.ticker, rank=rank+1, score=float(row.score))
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
            if ts.pullback_count_today >= limit: continue
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
    total_pnl = 0; total_trades = 0; daily = []
    wins = losses = spirals = 0
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
    return {"name": name, "limit": limit, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nPullback-Count-Limit Sweep ({len(dates)} days)\n")
configs = [
    ("Strict (Cameron) 2",  2),
    ("CURRENT 3",            3),
    ("Looser 4",             4),
    ("No-limit (sanity)",    99),
]
results = []
for name, n in configs:
    r = run_with_limit(name, n, dates)
    results.append(r)
    print(f"{name:<22} L={n:<2}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
