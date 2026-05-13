"""config_sweep.py — Vergleicht mehrere Pattern-Detector-Konfigurationen
gegen alle pilot trading days. Ranked nach Total PnL, Win-Rate, Sharpe-like.

Ergebnis-Tabelle zeigt welche Config die beste Balance hat.
"""
from __future__ import annotations
import sys
import logging
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod


@dataclass
class Config:
    name: str
    pole_min: float          # POLE_MIN_MOVE_PCT
    pole_topping: float      # POLE_TOPPING_TAIL_MAX
    flag_retrace: float      # FLAG_RETRACE_MAX_PCT
    vol_factor: float        # BREAKOUT_VOL_FACTOR

    def __str__(self):
        return f"pole={self.pole_min}% topping={self.pole_topping} retrace={self.flag_retrace}% vol={self.vol_factor}x"


# Configs to compare
CONFIGS = [
    Config("BASELINE",         pole_min=5.0, pole_topping=0.4, flag_retrace=50.0, vol_factor=1.5),
    Config("looser-pole",      pole_min=4.0, pole_topping=0.4, flag_retrace=50.0, vol_factor=1.5),
    Config("stricter-pole",    pole_min=6.0, pole_topping=0.4, flag_retrace=50.0, vol_factor=1.5),
    Config("higher-topping",   pole_min=5.0, pole_topping=0.5, flag_retrace=50.0, vol_factor=1.5),
    Config("flag-60",          pole_min=5.0, pole_topping=0.4, flag_retrace=60.0, vol_factor=1.5),
    Config("vol-strict",       pole_min=5.0, pole_topping=0.4, flag_retrace=50.0, vol_factor=2.0),
    Config("vol-loose",        pole_min=5.0, pole_topping=0.4, flag_retrace=50.0, vol_factor=1.2),
    Config("all-loose",        pole_min=4.0, pole_topping=0.5, flag_retrace=60.0, vol_factor=1.2),
    Config("all-strict",       pole_min=6.0, pole_topping=0.3, flag_retrace=45.0, vol_factor=2.0),
    Config("looser-pole+top",  pole_min=4.0, pole_topping=0.5, flag_retrace=50.0, vol_factor=1.5),
]


@dataclass
class Result:
    config_name: str
    total_pnl: float
    total_trades: int
    win_days: int
    loss_days: int
    no_trade_days: int
    spiral_days: int
    win_rate: float       # of decided days
    avg_per_trade: float
    best_day: float
    worst_day: float
    max_drawdown: float


def apply_config(cfg: Config):
    bot_mod.POLE_MIN_MOVE_PCT = cfg.pole_min
    bot_mod.POLE_TOPPING_TAIL_MAX = cfg.pole_topping
    bot_mod.FLAG_RETRACE_MAX_PCT = cfg.flag_retrace
    bot_mod.BREAKOUT_VOL_FACTOR = cfg.vol_factor


def save_baseline():
    return (
        bot_mod.POLE_MIN_MOVE_PCT,
        bot_mod.POLE_TOPPING_TAIL_MAX,
        bot_mod.FLAG_RETRACE_MAX_PCT,
        bot_mod.BREAKOUT_VOL_FACTOR,
    )


def restore_baseline(snap):
    (bot_mod.POLE_MIN_MOVE_PCT,
     bot_mod.POLE_TOPPING_TAIL_MAX,
     bot_mod.FLAG_RETRACE_MAX_PCT,
     bot_mod.BREAKOUT_VOL_FACTOR) = snap


def run_config(cfg: Config, dates: list) -> Result:
    """Run all dates with given config."""
    bot_mod.log.setLevel(logging.ERROR)
    apply_config(cfg)
    daily_pnl = []
    total_trades = 0
    win_days = loss_days = no_trade_days = spiral_days = 0
    for d in dates:
        rb = bot_mod.ReplayBot()
        try:
            rb.run(d.isoformat())
        except Exception:
            continue
        pnl = round(rb.day.realized_pnl, 2)
        daily_pnl.append(pnl)
        total_trades += rb.day.trades_completed_today
        if rb.day.spiral_locked:
            spiral_days += 1
        if rb.day.trades_completed_today == 0:
            no_trade_days += 1
        elif pnl > 0:
            win_days += 1
        elif pnl < 0:
            loss_days += 1

    total_pnl = sum(daily_pnl)
    decided = win_days + loss_days
    # Drawdown
    cum = 0
    peak = 0
    max_dd = 0
    for p in daily_pnl:
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    return Result(
        config_name=cfg.name,
        total_pnl=total_pnl,
        total_trades=total_trades,
        win_days=win_days,
        loss_days=loss_days,
        no_trade_days=no_trade_days,
        spiral_days=spiral_days,
        win_rate=(win_days / decided * 100) if decided else 0,
        avg_per_trade=(total_pnl / total_trades) if total_trades else 0,
        best_day=max(daily_pnl) if daily_pnl else 0,
        worst_day=min(daily_pnl) if daily_pnl else 0,
        max_drawdown=max_dd,
    )


def main():
    bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
    bars = pd.read_parquet(bars_path)
    tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
    bars[tc] = pd.to_datetime(bars[tc], utc=True)
    dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

    print(f"\n{'='*100}")
    print(f"CONFIG SWEEP — {len(CONFIGS)} configs x {len(dates)} pilot days")
    print(f"{'='*100}\n")

    baseline = save_baseline()
    results = []
    try:
        for cfg in CONFIGS:
            print(f"Running {cfg.name}: {cfg} ...", end="", flush=True)
            r = run_config(cfg, dates)
            results.append(r)
            print(f" PnL ${r.total_pnl:+7.2f}  trades {r.total_trades:>2}  win-rate {r.win_rate:.0f}%")
    finally:
        restore_baseline(baseline)

    # Print ranking table
    print(f"\n{'='*100}")
    print(f"{'Config':<18} {'Trd':>4} {'PnL':>9} {'Avg/Trd':>9} {'Win%':>6} {'Best':>8} {'Worst':>8} {'MaxDD':>9} {'Spiral':>7}")
    print(f"{'-'*100}")
    # Sort by PnL desc
    for r in sorted(results, key=lambda x: -x.total_pnl):
        is_baseline = r.config_name == "BASELINE"
        marker = "*" if is_baseline else " "
        print(f"{marker}{r.config_name:<17} {r.total_trades:>4} "
              f"${r.total_pnl:>+7.2f} ${r.avg_per_trade:>+7.2f} "
              f"{r.win_rate:>5.0f}% ${r.best_day:>+6.2f} ${r.worst_day:>+6.2f} "
              f"${r.max_drawdown:>+7.2f}  {r.spiral_days:>5}")
    print(f"{'-'*100}")
    print(f"* = BASELINE (production config)")

    # Highlight best by different metrics
    print(f"\nBest by metric:")
    by_pnl = max(results, key=lambda x: x.total_pnl)
    by_winrate = max([r for r in results if (r.win_days+r.loss_days) > 0],
                     key=lambda x: x.win_rate, default=None)
    by_avg = max(results, key=lambda x: x.avg_per_trade)
    # Sharpe-like: pnl / |max_drawdown| (higher = better risk-adj)
    def sharpe_like(r):
        if abs(r.max_drawdown) < 0.01:
            return r.total_pnl  # no dd = great
        return r.total_pnl / abs(r.max_drawdown)
    by_sharpe = max(results, key=sharpe_like)

    print(f"  Total PnL:    {by_pnl.config_name:<18} ${by_pnl.total_pnl:+.2f}")
    if by_winrate:
        print(f"  Win Rate:     {by_winrate.config_name:<18} {by_winrate.win_rate:.0f}%")
    print(f"  Avg/Trade:    {by_avg.config_name:<18} ${by_avg.avg_per_trade:+.2f}")
    print(f"  Sharpe-like:  {by_sharpe.config_name:<18} ratio {sharpe_like(by_sharpe):.2f}")

    # Show baseline rank
    sorted_by_pnl = sorted(results, key=lambda x: -x.total_pnl)
    baseline_r = next((r for r in results if r.config_name == "BASELINE"), None)
    if baseline_r:
        rank_pnl = sorted_by_pnl.index(baseline_r) + 1
        print(f"\nBaseline rank by PnL: #{rank_pnl} of {len(results)}")


if __name__ == "__main__":
    main()
