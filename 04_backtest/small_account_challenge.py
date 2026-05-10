"""
small_account_challenge.py — Cameron-Style Small-Account-Sim auf Pilot-Daten.

Simuliert verschiedene Szenarien:
  - Start-Kapital
  - Leverage (Margin)
  - Max trades pro Tag
  - Rank-Filter (welche Top-N nehmen)

Cameron-Rule: 1 Trade pro Tag, full buying power, max-size.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
import pandas as pd
import numpy as np

D = Path(__file__).resolve().parent / "data_pilot"


def simulate(trades_path, start_cap, leverage, max_trades_per_day,
             rank_min=None, rank_max=None, max_pos_pct=1.0,
             liquidity_cap_shares_pct_of_volume=0.01,
             commission_per_trade=0.0):
    """Run small-account simulation. Returns history + summary."""
    df = pd.read_parquet(trades_path).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values(["date", "entry_time"])

    if rank_min is not None:
        df = df[df["rank_in_day"] >= rank_min]
    if rank_max is not None:
        df = df[df["rank_in_day"] <= rank_max]
    df = df.groupby("date").head(max_trades_per_day)

    capital = start_cap
    history = [(df["date"].min() if len(df) else None, capital)]
    busted = False
    bust_date = None

    for date, day_trades in df.groupby("date"):
        for _, t in day_trades.iterrows():
            if capital <= 0:
                busted = True; bust_date = date; break
            bp = capital * leverage * max_pos_pct
            shares = int(bp / t["entry_price"])
            # Liquidity-Cap: max 1% of avg-volume not exceeded (proxy: skip if too few avg shares)
            if shares < 1:
                continue
            pnl = shares * t["pnl_per_share"] - commission_per_trade
            capital += pnl
        history.append((date, capital))
        if busted: break

    if not busted and len(df):
        bust_date = None
    return {
        "start_cap": start_cap,
        "end_cap": capital,
        "total_return_pct": (capital / start_cap - 1) * 100,
        "n_trades": len(df) if not busted else "n/a",
        "n_days": len(history) - 1,
        "busted": busted,
        "bust_date": str(bust_date) if bust_date else None,
        "history": history,
        "min_equity": min(h[1] for h in history),
        "max_drawdown_pct": (min(h[1] for h in history) - start_cap) / start_cap * 100,
    }


def annualize(pct, days):
    if days <= 0: return 0
    return ((1 + pct / 100) ** (252 / days) - 1) * 100


SCENARIOS = [
    # (label, start_cap, leverage, max_trades, rank_min, rank_max, max_pos_pct)
    ("Cameron-pure: $583, 4x Margin, 1 trade/day, Top-1",
     583, 4, 1, 1, 1, 1.0),
    ("Cameron-pure: $583, 4x Margin, 1 trade/day, Rank 2-7 (Sweet-Spot)",
     583, 4, 1, 2, 7, 1.0),
    ("Realistic Beginner: $5.000, 4x Margin, 1 trade/day, Rank 2-7",
     5_000, 4, 1, 2, 7, 1.0),
    ("Realistic PDT: $25.000, 4x Margin, 1 trade/day, Rank 2-7",
     25_000, 4, 1, 2, 7, 1.0),
    ("Realistic PDT: $25.000, 4x Margin, 3 trades/day, Rank 2-7",
     25_000, 4, 3, 2, 7, 1.0),
    ("Conservative: $25.000, kein Leverage, 1 trade/day, 50% Position",
     25_000, 1, 1, 2, 7, 0.5),
    ("Aggressive Offshore: $1.000, 6x Margin, 1 trade/day, Rank 2-7",
     1_000, 6, 1, 2, 7, 1.0),
]

print("=" * 95)
print("CAMERON SMALL-ACCOUNT-CHALLENGE — Pilot-Simulation auf 60-Tage-Daten")
print("=" * 95)
print()

for (lbl, sc, lev, mt, rn_min, rn_max, mpp) in SCENARIOS:
    r = simulate(
        D / "trades_v3_top10.parquet",
        start_cap=sc, leverage=lev, max_trades_per_day=mt,
        rank_min=rn_min, rank_max=rn_max, max_pos_pct=mpp,
    )
    pa = annualize(r["total_return_pct"], r["n_days"])
    bust_str = f" [BUSTED am {r['bust_date']}]" if r["busted"] else ""
    print(f"\n{lbl}{bust_str}")
    print(f"  Start: ${r['start_cap']:,.0f}  End: ${r['end_cap']:,.0f}  "
          f"Return: {r['total_return_pct']:+.0f}%  ({r['n_days']}d)")
    print(f"  Annualisiert: {pa:+.0f}%/Jahr compound")
    print(f"  Min-Equity: ${r['min_equity']:,.0f}  Max-DD: {r['max_drawdown_pct']:+.1f}%")

print()
print("=" * 95)
print("CAMERON ECHT (zur Kalibrierung):")
print("  $583 → $100k in 45 Tagen  (Sim-Modus, kleine Größe)")
print("  $583 → $335k Ende 2017  (~+57.000% p.a. compound)")
print("  $583 → $1M  Ende 2019  (~+1.840% p.a. compound über 2,5 Jahre)")
print("  Lifetime: ~+220% p.a. compound über 7+ Jahre")
print("=" * 95)
