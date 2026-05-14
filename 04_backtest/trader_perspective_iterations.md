# Trader-Perspective Iterations Log

Track each hypothesis tested via the 20-min /loop so we don't repeat work.

**Live baseline (167-day pilot, 2026-05-14):**
- PnL **$581.82**, 17 trades, win-days 13 / loss-days 3, WR 81%, MaxDD -$50.25
- Sharpe-like (PnL / |MaxDD|) ≈ **11.6**

---

## Iter 1 — Earlier entry cutoff (Cameron Power-Hour fidelity)
- **Date:** 2026-05-14
- **Looked at:** `TIME_NEW_ENTRIES_END` (currently `11:30`) with `TIME_HARD_FLAT = 12:00`. Trader concern: entries at 11:25-11:29 only get 30-35 min before forced eod_exit. Cameron classic: don't enter without 60+ min runway.
- **Hypothesis:** Cut entry window earlier → higher WR, less eod-forced losses → better Sharpe.
- **Backtest (167 days, full pilot):**
  | Cutoff | PnL | Trades | WR | MaxDD | Sharpe-like |
  |---|---|---|---|---|---|
  | 11:30 (baseline) | **+$581.82** | 17 | 81% | -$50.25 | **11.6** |
  | 11:15 | +$402.94 | 14 | 85% | -$37.10 | 10.9 |
  | 11:00 | +$241.27 | 11 | 80% | -$37.10 | 6.5 |
  | 10:30 (Power-only) | +$153.69 | 8 | 88% | -$37.10 | 4.2 |
- **Verdict: SKIP.** Cameron's classic 11:00-cutoff intuition raises WR but COSTS $340 of net PnL by killing 6 profitable late-window trades. MaxDD only improves $13. Sharpe-like is best at the existing 11:30. Do NOT tighten.
- **Insight saved:** The bot's 11:00-11:30 window IS its edge, not its leak. If a future iter wants to attack late-window risk, do it via *position-management* (e.g. tighter stop for entries after 11:00), not via entry-rejection.

## Open ideas (queue for future iterations)
- Conditional position-size reduction for entries after 11:00 (not full block).
- POLE_TOPPING_TAIL_MAX 0.5 — user-decision pending (config-sweep showed +$115 historically, but baseline has since grown to $581.82 — re-test under current code).
- RSI-extension veto (>80 = no entry) — Cameron-classic "don't chase extended".
- Trailing-stop after T1 instead of fixed BE.
- Adaptive QE-distance based on ATR/volatility instead of fixed 30c.
- Float-based size tier (low-float runners get smaller size for slippage).
- VWAP-hold confirmation (entry only if price > VWAP on signal bar).
