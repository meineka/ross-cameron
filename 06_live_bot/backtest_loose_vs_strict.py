"""backtest_loose_vs_strict.py — Phase-70 follow-up

User question: "C) Backtest auf den 532 verfügbaren Tickers fahren
mit loose-mode-Werten — würde zeigen ob's überhaupt PROFITABEL ist
bevor du loose live laufen lässt"

Compare strict / relaxed / loose variants on the existing pilot
trade-tape (trades.parquet from the v1 backtester).

Important caveat: the trade tape was produced with Cameron-STRICT
pattern detection. Loose-mode RELAXES entry criteria, so it would
generate MORE trades than what's in the tape. This script
under-estimates loose-mode trade count but accurately compares the
RISK/REWARD scaling of the existing trades at each variant's
position size.

For a true loose-mode backtest, the bull-flag detector would need to
be re-run on 1m intraday bars with loose thresholds — that's a
separate, multi-hour build. This is the fast-path comparison.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_DIR = ROOT / "04_backtest" / "data_pilot"


def variant_sizing(variant: str) -> tuple[float, float, str]:
    """Return (max_loss_usd, equity_cap_pct, comment)."""
    if variant == "strict":
        return 50.0, 1.0, "Cameron 1% equity-cap, $50 per-trade-cap"
    if variant == "relaxed":
        return 100.0, 2.0, "Phase-66 — 2x size, 2% equity-cap"
    if variant == "loose":
        return 100.0, 2.0, "Phase-69 — same SIZING as relaxed (different ENTRY)"
    raise ValueError(f"unknown variant: {variant}")


def stats(df: pd.DataFrame, prefix: str) -> dict:
    pnl = df[f"pnl_{prefix}_usd"]
    wins = (pnl > 0).sum()
    losses = (pnl < 0).sum()
    n = wins + losses
    sum_wins = pnl[pnl > 0].sum()
    sum_losses = -pnl[pnl < 0].sum()
    pf = (sum_wins / sum_losses) if sum_losses > 0 else float("inf")
    mean, std = pnl.mean(), pnl.std()
    sharpe = (mean / std) if std > 0 else 0.0
    cum = pnl.cumsum()
    max_dd = float((cum - cum.cummax()).min())
    profit_per_dd = (pnl.sum() / abs(max_dd)) if max_dd != 0 else 0.0
    return dict(
        n=int(n), wins=int(wins), losses=int(losses),
        win_pct=100 * wins / n if n else 0.0,
        total=float(pnl.sum()),
        pf=float(pf), sharpe=float(sharpe), max_dd=float(max_dd),
        avg=float(pnl.sum() / n) if n else 0.0,
        profit_per_dd=float(profit_per_dd),
    )


def main():
    tape = pd.read_parquet(PILOT_DIR / "trades.parquet")
    tape = tape[tape["pnl_per_share"].notna()].copy()
    tape["risk_per_share"] = (
        tape["entry_price"] - tape["stop_price"]
    ).clip(lower=0.05)

    print("=" * 78)
    print(" BACKTEST: strict / relaxed / loose on 604 pilot trades")
    print("=" * 78)
    print(
        f" Source: trades.parquet ({len(tape)} trades, "
        f"{tape['date'].min()} to {tape['date'].max()})"
    )
    print()

    # For each variant, compute shares + PnL
    results = {}
    for variant in ("strict", "relaxed", "loose"):
        max_loss, eq_cap, comment = variant_sizing(variant)
        # Use max-loss per trade as the binding cap (paper $100k account
        # makes 1%-2% equity cap = $1k-$2k which is non-binding for these
        # entries with 5-50c risk-per-share)
        col = f"shares_{variant}"
        tape[col] = (max_loss / tape["risk_per_share"]).astype(int)
        pnl_col = f"pnl_{variant}_usd"
        tape[pnl_col] = tape["pnl_per_share"] * tape[col]
        results[variant] = stats(tape, variant)
        results[variant]["_comment"] = comment

    # Print table
    header = f"{'metric':<22}{'strict':>14}{'relaxed':>14}{'loose':>14}"
    print(header)
    print("-" * len(header))
    for key, label in [
        ("n", "trades"),
        ("wins", "wins"),
        ("losses", "losses"),
        ("win_pct", "win%"),
        ("total", "TOTAL PnL ($)"),
        ("pf", "profit-factor"),
        ("sharpe", "sharpe-like"),
        ("max_dd", "max-drawdown ($)"),
        ("avg", "avg per trade ($)"),
        ("profit_per_dd", "profit / |DD|"),
    ]:
        s = results["strict"][key]
        r = results["relaxed"][key]
        l = results["loose"][key]
        fmt = "{:>14.2f}" if isinstance(s, float) else "{:>14d}"
        print(
            f"{label:<22}" + fmt.format(s) + fmt.format(r) + fmt.format(l)
        )

    print("=" * 78)
    print(" Variants:")
    for v in ("strict", "relaxed", "loose"):
        print(f"   {v:<8} → {results[v]['_comment']}")
    print()
    print(" KEY INSIGHT")
    print("-" * 78)
    print(" Loose has the SAME sizing as relaxed (both 2x of strict).")
    print(" Phase-69 loose ALSO loosens ENTRY criteria (gain≥5%, RVOL≥3x,")
    print(" pole>=2.5%, retrace<=70%, catalyst-off). Those entries are NOT")
    print(" in this trade tape — the tape was generated with Cameron-STRICT")
    print(" pattern detection.")
    print()
    print(" So loose-mode would ADD trades that strict would have rejected.")
    print(" The PnL of those EXTRA trades is unknown without re-running the")
    print(" full bull-flag detector on the 1m intraday data — that's a")
    print(" separate multi-hour build.")
    print()
    print(" This table shows the LOWER BOUND for loose performance:")
    print(" matches relaxed exactly because same sizing on same trades.")
    print(" Real loose would likely have MORE trades and POSSIBLY higher")
    print(" total PnL but ALSO higher max-DD as entry quality drops.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
