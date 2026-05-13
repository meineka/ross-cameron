"""backtest_report.py — Full backtest over all pilot days with current config.

Reports: per-day trades + PnL, win/loss stats, drawdown, sharpe-like ratio,
plus baseline-comparison vs. test_replay_regression's MNTS-baseline.
"""
from __future__ import annotations
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import bot as bot_mod


def run_one_day(target_date: str) -> dict:
    """Replay one day with current config."""
    bot_mod.log.setLevel(logging.ERROR)
    rb = bot_mod.ReplayBot()
    try:
        rb.run(target_date)
    except Exception as e:
        return {"date": target_date, "error": str(e)[:80]}
    d = rb.day
    return {
        "date": target_date,
        "pnl": round(d.realized_pnl, 2),
        "peak": round(d.peak_pnl, 2),
        "trades": d.trades_completed_today,
        "consec_losses_final": d.consecutive_losses,
        "spiral": d.spiral_locked,
    }


def main():
    # Get all available dates from pilot data
    bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
    bars = pd.read_parquet(bars_path)
    tc = next(c for c in bars.columns if "time" in c.lower() or "date" in c.lower())
    bars[tc] = pd.to_datetime(bars[tc], utc=True)
    dates = sorted(bars[tc].dt.tz_convert("America/New_York").dt.date.unique())

    print(f"\n{'='*70}")
    print(f"CAMERON-BOT BACKTEST REPORT")
    print(f"{'='*70}")
    print(f"Period:     {dates[0]} - {dates[-1]} ({len(dates)} trading days)")
    print(f"Bar-Frame:  5-min aggregated (Cameron-standard)")
    print(f"Config:     POLE_MIN={bot_mod.POLE_MIN_MOVE_PCT}%  "
          f"FLAG_RETRACE_MAX={bot_mod.FLAG_RETRACE_MAX_PCT}%")
    print(f"            BREAKOUT_VOL_FACTOR={bot_mod.BREAKOUT_VOL_FACTOR}x  "
          f"PRICE=${bot_mod.PRICE_MIN}-${bot_mod.PRICE_MAX}")
    print(f"            MAX_LOSS=${bot_mod.MAX_LOSS_PER_TRADE_USD}  "
          f"DAILY_GOAL=${bot_mod.DAILY_GOAL_USD}")
    print(f"{'='*70}\n")

    print(f"{'Date':<12} {'Trades':>7} {'PnL':>10} {'Peak':>10} {'Note':<20}")
    print(f"{'-'*70}")

    results = []
    for d in dates:
        ds = d.isoformat()
        r = run_one_day(ds)
        results.append(r)
        if "error" in r:
            print(f"{ds:<12} ERR: {r['error']}")
            continue
        note = ""
        if r["spiral"]:
            note = "spiral_locked"
        elif r["trades"] == 0:
            note = "no setup"
        elif r["pnl"] < 0:
            note = "loss"
        elif r["pnl"] >= bot_mod.DAILY_GOAL_USD:
            note = "GOAL"
        print(f"{ds:<12} {r['trades']:>7} ${r['pnl']:>8.2f} ${r['peak']:>8.2f}  {note}")

    print(f"{'-'*70}\n")

    # Aggregate stats
    valid = [r for r in results if "error" not in r]
    n_days = len(valid)
    total_pnl = sum(r["pnl"] for r in valid)
    total_trades = sum(r["trades"] for r in valid)
    win_days = sum(1 for r in valid if r["pnl"] > 0)
    loss_days = sum(1 for r in valid if r["pnl"] < 0)
    no_trade_days = sum(1 for r in valid if r["trades"] == 0)
    spiral_days = sum(1 for r in valid if r["spiral"])
    pnls = [r["pnl"] for r in valid]
    max_day_win = max(pnls) if pnls else 0
    max_day_loss = min(pnls) if pnls else 0

    # Drawdown calculation
    cum_pnl = 0
    peak_cum = 0
    max_drawdown = 0
    for r in valid:
        cum_pnl += r["pnl"]
        peak_cum = max(peak_cum, cum_pnl)
        max_drawdown = min(max_drawdown, cum_pnl - peak_cum)

    # Win rate
    decided_days = win_days + loss_days
    win_rate = (win_days / decided_days * 100) if decided_days else 0

    print(f"AGGREGATE")
    print(f"  Days analyzed:        {n_days}")
    print(f"  Total trades:         {total_trades}")
    print(f"  Total PnL:            ${total_pnl:+.2f}")
    print(f"  Avg PnL/day:          ${total_pnl/n_days:+.2f}")
    print(f"  Avg PnL/trade:        ${total_pnl/total_trades:+.2f}" if total_trades else "  Avg PnL/trade:        N/A")
    print(f"")
    print(f"  Win days:             {win_days}  ({win_days/n_days*100:.0f}% of all days)")
    print(f"  Loss days:            {loss_days}  ({loss_days/n_days*100:.0f}% of all days)")
    print(f"  No-trade days:        {no_trade_days}  ({no_trade_days/n_days*100:.0f}% of all days)")
    print(f"  Spiral-locked days:   {spiral_days}")
    print(f"")
    print(f"  Win rate (decided):   {win_rate:.1f}%")
    print(f"  Best day:             ${max_day_win:+.2f}")
    print(f"  Worst day:            ${max_day_loss:+.2f}")
    print(f"  Max cum. drawdown:    ${max_drawdown:+.2f}")
    print(f"  Final cumulative:     ${cum_pnl:+.2f}")
    print(f"")
    print(f"  Trade-Day Activity:   {(n_days-no_trade_days)/n_days*100:.0f}% of days saw trades")
    if n_days - no_trade_days > 0:
        avg_trades_per_active = total_trades / (n_days - no_trade_days)
        print(f"  Avg trades / active day: {avg_trades_per_active:.1f}")


if __name__ == "__main__":
    main()
