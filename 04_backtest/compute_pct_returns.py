"""compute_pct_returns.py — % Gain pro Trade + Annualisierung."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from pathlib import Path
import pandas as pd

D = Path(__file__).resolve().parent / "data_pilot"

def analyze(path, label):
    df = pd.read_parquet(path).copy()
    if df.empty: print(f"{label}: empty"); return
    df["pct_gain"] = df["pnl_per_share"] / df["entry_price"] * 100
    wins = df[df["pnl_per_share"] > 0]
    losses = df[df["pnl_per_share"] <= 0]
    n_days = pd.to_datetime(df["date"]).dt.date.nunique()
    print(f"\n=== {label} ===")
    print(f"  Trades: {len(df)} über {n_days} unique Trading-Tage")
    print(f"  Win-Rate: {len(wins)/len(df)*100:.1f}%")
    print(f"  Avg %-Gain pro Trade:    {df['pct_gain'].mean():+.3f}%")
    print(f"  Avg %-Gain pro WINNER:   {wins['pct_gain'].mean() if len(wins) else 0:+.3f}%")
    print(f"  Avg %-Gain pro LOSER:    {losses['pct_gain'].mean() if len(losses) else 0:+.3f}%")
    print(f"  Median %-Gain:           {df['pct_gain'].median():+.3f}%")
    print(f"  Best %:                  {df['pct_gain'].max():+.2f}%")
    print(f"  Worst %:                 {df['pct_gain'].min():+.2f}%")
    print(f"  Sum %-Gain (60d):        {df['pct_gain'].sum():+.2f}%")
    # Trades pro Tag
    tpd = len(df) / max(n_days, 1)
    print(f"  Avg Trades pro Tag:      {tpd:.1f}")

    # Annualisierung-Szenarien (252 Trading-Tage)
    print(f"\n  Annualisierung (252 Trading-Days, je Trade % aufs Konto):")
    for size_pct in [10, 20, 50, 100]:
        # Assumption: pro Trade size_pct% des Kontos eingesetzt
        scaled_pct = df['pct_gain'] * (size_pct / 100)
        total_60d = scaled_pct.sum()  # additiver Approx (sollte cumulativ sein, OK für Pilot)
        annualized = total_60d * (252 / max(n_days, 1))
        # Compounding (more accurate)
        cumulative = (1 + scaled_pct / 100).prod() - 1
        annualized_compound = (1 + cumulative) ** (252 / max(n_days, 1)) - 1
        print(f"    {size_pct:3d}% Kapital pro Trade: ~{annualized:+7.1f}% additiv | {annualized_compound*100:+7.1f}% compound")

# Compare all 3 result-files
for fn, lbl in [
    ("trades.parquet", "V1 baseline (alle 1026 ticker-days)"),
    ("trades_v2.parquet", "V2 fixed loose"),
    ("trades_v3.parquet", "V3 (last run config)"),
]:
    p = D / fn
    if p.exists(): analyze(p, lbl)
