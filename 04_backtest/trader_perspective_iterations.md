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

## Iter 3 — RSI extension/momentum veto (Cameron "don't chase extended")
- **Date:** 2026-05-14
- **Looked at:** Pre-entry RSI(14) gate on signal-bar close. Cameron-classic: avoid RSI > 80 (extended) and arguably also require RSI > 55 (momentum).
- **Hypothesis:** Adding an RSI window filter should improve risk-adjusted return by avoiding parabolic chases (high-RSI) and weak setups (low-RSI).
- **Backtest (167-day pilot, baseline = post-Iter-2):**
  | Filter | PnL | Trd | WR | DD | Sharpe-like |
  |---|---|---|---|---|---|
  | **None (baseline)** | **$778.60** | 19 | 83% | -$50.25 | **15.49** |
  | RSI > 85 veto | $758.14 | 18 | 83% | -$70.71 | 10.72 |
  | RSI > 80 veto | $645.75 | 17 | 82% | -$70.71 | 9.13 |
  | RSI > 75 veto | $391.78 | 10 | 80% | -$49.80 | 7.87 |
  | RSI > 70 veto | $167.42 | 8 | 75% | -$49.80 | 3.36 |
  | RSI ≥ 50 | $757.84 | 18 | 82% | -$50.25 | 15.08 |
  | RSI ≥ 55 | $667.05 | 14 | 85% | -$75.24 | 8.87 |
  | RSI ≥ 60 | $667.05 | 14 | 85% | -$75.24 | 8.87 |
  | RSI 50..80 | $624.99 | 16 | 81% | -$70.71 | 8.84 |
  | RSI 55..85 | $646.59 | 13 | 85% | -$95.70 | 6.76 |
- **Verdict: SKIP.** Every RSI window underperforms baseline on both PnL and Sharpe. The pattern detector already filters for momentum (green-pole, volume-rising, topping-tail veto) and extension (topping-tail, retrace). RSI on top is redundant — it cuts winners. MaxDD actually WORSENS with the veto because removing a winning trade lowers the peak before subsequent losses.
- **Insight saved:** Don't add another momentum/extension filter on top of the bull-flag pattern. Cameron's "RSI" rule is implicitly already encoded.

## Iter 4 — Trailing stop after T1 (instead of break-even)
- **Date:** 2026-05-14
- **Looked at:** Post-T1 stop logic. Currently `stop = entry_price` (pure BE). Cameron in his videos trails the stop to the recent swing-low after a partial — never lets profit turn negative. Test: stop = max(entry, lowest-low of last N bars), N ∈ {1..5}.
- **Hypothesis:** Trailing should reduce drawdowns on failed-T2 attempts and lock in some profit on choppy continuation. Net positive on Sharpe.
- **Backtest (167-day pilot, baseline = post-Iter-2):**
  | Trail-N | PnL | Trd | W/L | WR | DD | Sharpe-like |
  |---|---|---|---|---|---|---|
  | **0 (baseline BE)** | **$778.60** | 19 | 15/3 | **83%** | -$50.25 | **15.49** |
  | 1-bar | $220.51 | 21 | 10/10 | 50% | -$75.00 | 2.94 |
  | 2-bar | $372.38 | 21 | 11/9 | 55% | -$71.09 | 5.24 |
  | 3-bar | $391.34 | 21 | 13/7 | 65% | -$68.15 | 5.74 |
  | 4-bar | $392.09 | 21 | 12/8 | 60% | -$67.85 | 5.78 |
  | 5-bar | $399.33 | 21 | 12/8 | 60% | -$66.15 | 6.04 |
- **Verdict: SKIP.** Catastrophic. WR collapses from 83 → 50-65%. PnL drops 50-70%. Trade-count rises (19→21) because trail-stops fire on normal post-T1 chop.
- **Why it fails:** On a 5-min chart, after T1 fires the next 1-3 bars are often consolidation with lows above entry. A mechanical trailing stop ratchets ABOVE entry, then a normal pullback (still healthy structure) takes it out — killing the T2 runner. Cameron's discretionary "trail to consolidation low" is NOT the same as "trail to last-N-bar low" — he picks structure, not bar-count. Mechanical 5-min trailing on this universe destroys the T2-runner edge that drives most of the bot's PnL.
- **Insight saved:** BE-after-T1 is the right stop logic for this 5-min mechanical strategy. Future trailing experiments should be structure-based (swing-low after T1 confirmation), not bar-count-based.

## Open ideas (queue for future iterations)
- Conditional position-size reduction for entries after 11:00 (not full block).
- Trailing-stop after T1 instead of fixed BE.
- Adaptive QE-distance based on ATR/volatility instead of fixed 30c.
- Float-based size tier (low-float runners get smaller size for slippage).
- VWAP-hold confirmation already exists (`is_above_vwap`) — could test "close above VWAP" on signal bar vs current any-touch.
- Stricter `pole_min = 5.5` as defensive alternative: -$156 PnL but Sharpe 13.6 / WR 92% / DD -$45.90. Worth A/B testing live for risk-averse mode.
