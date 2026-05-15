"""Test POLE_VOLUME_RISING Toleranz-Sweep.

Aktuell: 2nd-half >= 1st-half * 0.9 (10% Slack).
Cameron: Volume MUST rise during pole — strict no-tolerance.
"""
from __future__ import annotations
import sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import bot as bot_mod

bot_mod.log.setLevel(logging.ERROR)

# Patch detect_bull_flag with varying tolerance
_ORIG_DETECT = bot_mod.detect_bull_flag


def make_detect(tol):
    def detect(bars):
        if len(bars) < bot_mod.POLE_MIN_CANDLES + bot_mod.FLAG_MIN_CANDLES + 5:
            return False, {}
        o = np.array([b["open"] for b in bars])
        h = np.array([b["high"] for b in bars])
        l = np.array([b["low"] for b in bars])
        c = np.array([b["close"] for b in bars])
        v = np.array([b["volume"] for b in bars])
        green = c > o
        rng = np.maximum(h - l, 1e-9)
        upper_wick = h - np.maximum(c, o)
        topping = upper_wick / rng
        vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
        i = len(bars) - 1
        if not green[i]: return False, {}
        if c[i] < bot_mod.PRICE_MIN or c[i] > bot_mod.PRICE_MAX: return False, {}
        if np.isnan(vol_sma[i]) or vol_sma[i] <= 0: return False, {}
        if v[i] < vol_sma[i] * bot_mod.BREAKOUT_VOL_FACTOR: return False, {}
        for fl in range(bot_mod.FLAG_MIN_CANDLES, bot_mod.FLAG_MAX_CANDLES + 1):
            for pl in range(bot_mod.POLE_MIN_CANDLES, bot_mod.POLE_MAX_CANDLES + 1):
                ps = i - fl - pl; pe = i - fl
                if ps < 0: continue
                if not green[ps:pe].all(): continue
                p_start = o[ps]; p_end = c[pe-1]
                if p_start <= 0: continue
                p_pct = (p_end - p_start) / p_start * 100
                if p_pct < bot_mod.POLE_MIN_MOVE_PCT: continue
                if topping[ps:pe].max() > bot_mod.POLE_TOPPING_TAIL_MAX: continue
                # CUSTOM TOLERANCE
                if bot_mod.POLE_VOLUME_RISING_REQUIRED and pl >= 4:
                    first_half_vol = v[ps:ps+pl//2].mean()
                    second_half_vol = v[ps+pl//2:pe].mean()
                    if second_half_vol < first_half_vol * tol:
                        continue
                fs = pe; fe = i
                p_h = p_end - p_start
                if p_h <= 0: continue
                fl_low = l[fs:fe].min()
                retrace_amt = p_end - fl_low
                if retrace_amt < 0: continue
                if retrace_amt / p_h * 100 > bot_mod.FLAG_RETRACE_MAX_PCT: continue
                prh = h[fs:fe].max()
                if h[i] <= prh: continue
                ep = prh + bot_mod.SLIPPAGE_CENTS
                sp = fl_low - bot_mod.SLIPPAGE_CENTS
                if ep <= sp: continue
                if ep > 0:
                    risk_pct = (ep - sp) / ep * 100
                    if risk_pct > bot_mod.MAX_RISK_PCT:
                        return False, {"_veto": f"risk_{risk_pct:.1f}%"}
                t2_mech = ep + p_h
                if bot_mod.USE_PSYCH_LEVEL_T2:
                    next_half = (int(ep * 2) + 1) / 2.0
                    t2 = max(t2_mech, next_half) if next_half > ep + 0.05 else t2_mech
                else:
                    t2 = t2_mech
                risk = ep - sp
                if risk > 0 and (t2 - ep) / risk > bot_mod.MAX_POLE_T2_R:
                    return False, {"_veto": "pole_t2r"}
                if not bot_mod.is_above_vwap(bars, c[i]): return False, {"_veto": "vwap"}
                if not bot_mod.macd_is_bullish(c.tolist()): return False, {"_veto": "macd"}
                vetoed, why = bot_mod.false_breakout_veto(bars)
                if vetoed: return False, {"_veto": f"fbo_{why}"}
                return True, {
                    "entry_price": float(ep), "stop_price": float(sp),
                    "target1": float(ep + (ep - sp)), "target2": float(t2),
                    "pole_height": float(p_h),
                    "pole_candles": int(pl), "flag_candles": int(fl),
                }
        return False, {}
    return detect


def run(name, tol, dates):
    _orig = bot_mod.detect_bull_flag
    bot_mod.detect_bull_flag = make_detect(tol)
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
        bot_mod.detect_bull_flag = _orig
    cum = peak = mdd = 0
    for p in daily:
        cum += p; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    wr = round(wins/(wins+losses)*100, 0) if (wins+losses) else 0
    sharpe = round(total_pnl/abs(mdd), 2) if abs(mdd) > 0.01 else total_pnl
    return {"name": name, "tol": tol, "trades": total_trades,
            "pnl": round(total_pnl, 2), "win_rate": wr,
            "max_dd": round(mdd, 2), "spirals": spirals, "sharpe": sharpe}


bars_path, _ = bot_mod.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

print(f"\nPOLE_VOLUME_RISING Toleranz-Sweep ({len(dates)} days)\n")
configs = [
    ("Looser 0.7",     0.7),
    ("Looser 0.8",     0.8),
    ("CURRENT 0.9",    0.9),
    ("Strict 1.0",     1.0),
    ("Cameron 1.2",    1.2),
    ("Very strict 1.5",1.5),
]
results = []
for name, t in configs:
    r = run(name, t, dates)
    results.append(r)
    print(f"{name:<22}: trades={r['trades']:>2} pnl=${r['pnl']:>+7.2f} "
          f"win%={r['win_rate']:.0f} dd=${r['max_dd']:>+6.2f} sharpe={r['sharpe']}")

print(f"\n{'Config':<22} {'Trd':>4} {'PnL':>9} {'Win%':>6} {'MaxDD':>9} {'Sharpe':>7}")
print("-" * 72)
for r in sorted(results, key=lambda x: -x['sharpe']):
    marker = '*' if 'CURRENT' in r['name'] else ' '
    print(f"{marker}{r['name']:<21} {r['trades']:>4} "
          f"${r['pnl']:>+7.2f}  {r['win_rate']:>4.0f}%  "
          f"${r['max_dd']:>+7.2f}  {r['sharpe']:>6.2f}")
