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

## Iter 5 — Quick-Exit threshold tuning
- **Date:** 2026-05-14
- **Looked at:** `QUICK_EXIT_THRESHOLD_CENTS` (currently 0.30 = Cameron's spoken "30c quick exit"). Trader concern: the bot's universe is $2-$20 stocks; 30c against entry on a $3 ticker is 10% — too much rope. Tested fixed alternatives and risk-proportional `factor * (entry-stop)`.
- **Hypothesis:** Tighter QE clips small losers faster without hurting winners; proportional QE should be even better because it adapts to the trade's own stop-distance.
- **Backtest (167-day pilot, post-Iter-2 baseline):**
  | QE Setting | PnL | Trd | WR | DD | Sharpe-like |
  |---|---|---|---|---|---|
  | 30c (baseline) | $778.60 | 19 | 83% | -$50.25 | 15.49 |
  | **20c (NEW)** | **$793.90** | 19 | 83% | **-$37.10** | **21.40** |
  | 25c | $786.25 | 19 | 83% | -$42.60 | 18.46 |
  | 35c+ | $774.77 | 19 | 83% | -$54.08 | 14.33 |
  | 15c | $671.69 | 19 | 78% | -$37.10 | 18.10 |
  | Prop 0.6×(entry-stop) | $765.47 | 20 | 74% | -$37.10 | 20.63 |
  | Prop 0.5×(entry-stop) | $730.38 | 20 | 68% | -$48.07 | 15.19 |
- **Verdict: COMMIT 20c.** Same PnL band, MaxDD -26%, Sharpe-like +38%. Proportional QE looked promising on DD but ate winners — fixed 20c is simpler and dominant. 15c too tight (turns winners into QE).
- **Changed:** `QUICK_EXIT_THRESHOLD_CENTS = 0.20`. Test rewritten to read the live constant instead of pinning 30c. YAML's `30c` is a verbatim Cameron quote in narrative text — left untouched.

## Iter 6 — Conditional size reduction for late-window entries
- **Date:** 2026-05-14
- **Looked at:** Iter-1's open question: late-window (11:00-11:30) entries had asymmetric risk (BIRD-style eod_exit losses). Iter 1 ruled out outright rejection. Iter 6 tests a softer "half size after 11:00" approach.
- **Hypothesis:** Smaller late-window size preserves edge but caps catastrophic eod_exit.
- **Backtest (167-day pilot, post-Iter-5 baseline):**
  | Variant | PnL | Trd | WR | DD | Sharpe-like |
  |---|---|---|---|---|---|
  | **No late cut (baseline)** | **$793.90** | 19 | 83% | -$37.10 | **21.40** |
  | cut 11:00, 0.75x | $705.84 | 19 | 83% | -$37.10 | 19.03 |
  | cut 11:00, 0.50x | $615.97 | 19 | 83% | -$37.10 | 16.60 |
  | cut 11:00, 0.25x | $527.71 | 19 | 83% | -$37.10 | 14.22 |
  | cut 10:30, 0.75x | $655.71 | 19 | 83% | -$37.10 | 17.67 |
  | cut 10:30, 0.50x | $516.17 | 19 | 83% | -$37.10 | 13.91 |
- **Verdict: SKIP.** Every cut reduces PnL monotonically with the multiplier, with **zero** improvement to MaxDD or WR. The 11:00-11:30 entries are net-positive and the catastrophic-late-entry risk that motivated Iter 1 was already neutralized upstream by Iter 5's tighter QE (DD already at -$37.10, down from -$50.25). Layering a second defense against the same risk pays for protection twice without buying anything.
- **Meta-insight:** Each iteration's commit reshapes the baseline. The Iter-1 finding "11:00-11:30 entries are profitable but volatile" was true at the OLD baseline; after Iter 5, the volatility was clipped via QE. **Don't re-test against stale baselines** — re-evaluate queue items against current state. Closing the late-window question for now.

## Iter 7 — Local-optimum verification sweep
- **Date:** 2026-05-14
- **Looked at:** Three open queue items at once: finer `POLE_MIN` grid (3.0-4.5), `T2_R_MULTIPLE` (1.5-3.5), and `is_above_vwap` strictness (close vs low vs +0.5% buffer).
- **Hypothesis:** Some of these may still have local gradient unexplored.
- **Backtest (167-day pilot, post-Iter-5 baseline = $793.90 / Sharpe 21.40):**

  *Finer POLE_MIN grid:*
  | pole_min | PnL | Trd | WR | DD | Sharpe-like |
  |---|---|---|---|---|---|
  | 3.0 | $877.68 | 21 | 80% | -$62.95 | 13.94 |
  | 3.5 | $765.90 | 20 | 79% | -$62.95 | 12.17 |
  | **4.0 (current)** | **$793.90** | 19 | 83% | -$37.10 | **21.40** |
  | 4.5 | $681.40 | 18 | 82% | -$37.10 | 18.37 |
  | 5.0 | $597.12 | 17 | 81% | -$37.10 | 16.09 |

  *T2_R_MULTIPLE:* current is 3.5 (not 2.5 as the old comment suggested).
  | T2_R | PnL | Sharpe-like |
  |---|---|---|
  | 1.5 | $486.97 | 13.13 |
  | 2.5 | $629.88 | 15.92 |
  | 3.0 | $703.38 | 18.88 |
  | **3.5 (current)** | **$793.90** | **21.40** |

  *VWAP strictness:*
  | Rule | PnL | Sharpe-like |
  |---|---|---|
  | **close > VWAP (current)** | **$793.90** | **21.40** |
  | low > VWAP (stricter) | $773.24 | 20.84 |
  | close > VWAP × 1.005 | $793.90 | 21.40 (non-binding) |

- **Verdict: SKIP all three** — current values are at the local Sharpe optimum.
  - `pole_min=3.0` raises gross PnL by $84 but MaxDD jumps from $37→$63 and Sharpe collapses 35%. Bad risk-adjusted trade-off.
  - `T2_R=3.5` dominates lower values. Code comment about "2.5R optimal" was from an old baseline; current sweep confirms 3.5 is the right number now.
  - VWAP stricter (`low > VWAP`) cuts a winner. The +0.5% buffer is non-binding (signal bars already comfortably above VWAP at trigger time).
- **Insight saved:** The bot is now at a tight local optimum across pole_min, T2_R, QE, and VWAP. Further linear-parameter tuning will produce diminishing returns; future iters should test STRUCTURAL changes (new gates, new signals, new exit logic) rather than knob-twiddling.

## Open ideas (queue for future iterations)
- Trailing-stop after T1 instead of fixed BE.
- Adaptive QE-distance based on ATR/volatility instead of fixed 30c.
- Float-based size tier (low-float runners get smaller size for slippage).
- VWAP-hold confirmation already exists (`is_above_vwap`) — could test "close above VWAP" on signal bar vs current any-touch.
- Stricter `pole_min = 5.5` as defensive alternative: -$156 PnL but Sharpe 13.6 / WR 92% / DD -$45.90. Worth A/B testing live for risk-averse mode.
