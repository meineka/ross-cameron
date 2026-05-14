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

## Iter 2 — Multi-lever pattern-config sweep
- **Date:** 2026-05-14
- **Looked at:** `POLE_TOPPING_TAIL_MAX` (queued), plus all 4 pattern levers (pole_min, topping, flag_retrace, vol_factor).
- **Findings:**
  - `POLE_TOPPING_TAIL_MAX = 0.5` is **already in production** — the queued ticket was stale. On the current pilot, 0.4 / 0.5 / 0.6 yield identical PnL (non-binding range). Closing this open item.
  - `flag_retrace` and `vol_factor` sweeps showed no positive direction (40-50 identical; 55-60 worse; vol_factor 1.2-2.5 worse or break-even).
  - **`POLE_MIN_MOVE_PCT` 5.0 → 4.0 is a clear winner:**
    | Setting | PnL | Trd | W/L | WR | DD | Sharpe-like |
    |---|---|---|---|---|---|---|
    | 5.0 (was) | $581.82 | 17 | 13/3 | 81% | -$50.25 | 11.58 |
    | **4.0 (NEW)** | **$778.60** | 19 | 15/3 | 83% | -$50.25 | **15.49** |
    | 5.5 | $622.85 | 13 | 12/1 | 92% | -$45.90 | 13.57 |
    | 6.0 | $597.85 | 12 | 11/1 | 92% | -$45.90 | 13.03 |
    | 7.0 | $308.95 | 7 | 6/1 | 86% | -$45.90 | 6.73 |
- **Hypothesis:** Cameron-spoken threshold is "5%+ daily-gainers" but observed entries in his videos include 3-4% pole moves when the bull-flag is clean. Loosening 5.0→4.0 catches 2 high-quality setups on this pilot, both winners.
- **Verdict: COMMIT.** +$196.78 (+34%), +34% Sharpe, same MaxDD, no added losses. Small-sample caveat (only +2 trades) — if live tape disagrees over weeks, revert.
- **Changed:** `POLE_MIN_MOVE_PCT = 4.0` in `06_live_bot/bot.py`; matching update in `03_rules_engine/constraints.yaml`.

## Open ideas (queue for future iterations)
- Conditional position-size reduction for entries after 11:00 (not full block).
- RSI-extension veto (>80 = no entry) — Cameron-classic "don't chase extended".
- Trailing-stop after T1 instead of fixed BE.
- Adaptive QE-distance based on ATR/volatility instead of fixed 30c.
- Float-based size tier (low-float runners get smaller size for slippage).
- VWAP-hold confirmation already exists (`is_above_vwap`) — could test "close above VWAP" on signal bar vs current any-touch.
- Stricter `pole_min = 5.5` as defensive alternative: -$156 PnL but Sharpe 13.6 / WR 92% / DD -$45.90. Worth A/B testing live for risk-averse mode.
