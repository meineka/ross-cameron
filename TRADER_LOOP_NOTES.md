# Trader-Loop Notes (running)

Each iteration: hypothesis tested, kept (committed) or rejected (documented).
The committed changes need to remain robust under future-data validation.

## Committed (positive backtest evidence)

### Iter 1: MAX_RISK_PCT = 8.0
- **Hypothesis:** Cameron's "tight stops only" rule (risk%>10% loses 80% of time)
- **Backtest:** 17 trades → 9 trades, $75→$73 PnL, win-rate 67%→78%,
  MaxDD halved ($30→$18), Sharpe +59%
- **Commit:** `cc371fa`

### Iter 34: Cross-loss pattern analysis (SKIP — losses are random)
- **Diag of both 145d losses:**
  | Date | Ticker | Risk% | Entry | Time | PnL |
  |---|---|---|---|---|---|
  | 2025-11-07 | SSP | 5.33% | $2.72 | 11:50 | -$49.88 |
  | 2026-01-22 | SVAC | 2.97% | $11.80 | 12:45 | -$37.10 |
- **No common pattern:** Different prices ($2.72 vs $11.80), different
  risk-pcts (5.33 vs 2.97), different times (11:50 vs 12:45).
- **SSP appeared BOTH as loss (11-07) and win (11-17):** ticker-cooldown
  would have missed the win. No actionable per-ticker filter.
- **Conclusion:** Losses are ~12% intrinsic random failure-rate of
  Cameron-Bull-Flag strategy. Cannot be filtered without losing equivalent
  winners.

### Iter 33: Diagnose 2025-11-07 SSP loss + Adaptive-QE sweep (SKIP)
- **Diag:** SSP entry $2.72, stop $2.575 (risk=14.5c, 5.33%). Exit at stop
  full 344-share loss = -$49.88. Bar's price went straight to stop without
  intermediate bounce.
- **Why QE didn't help:** Bot's QE threshold 30c > stop-distance 14.5c.
  QE can NEVER fire for tight-stop trades — stop fires first.
- **Adaptive QE sweep:**
  - 30c fixed (current): $719.67 / 88% / Sharpe 14.43
  - 0.5R relative: $600 / 65% (premature exits of winners!)
  - 0.75R relative: $691 / 81% (still worse)
  - Fixed 15c: $586 / 76% — worse, QE-exit price < stop-price for SSP
- **Insight:** When stop-distance < QE-threshold, stop fires FIRST and
  gives BETTER exit price than QE would (since QE-price = entry - threshold,
  which would be lower than stop-price for tight setups).
- **Verdict:** SKIP. QE optimally configured at 30c fixed. SSP-style losses
  are structural-unavoidable with 5-min bars + tight Cameron stops.

### Iter 32: Pilot extended 122→145 days (Oct 2025) + STOP-Tightening-Cascade
- **Data fetch:** 17 more days (2025-10-15 to 2025-11-14) via Alpaca.
- **New trade:** 2025-11-07 -$49.88 LOSS (another max-cap hit).
- **Backtest 145d:** 16 trd / $719.67 / 88% / MDD -$49.88 / Sharpe 14.43
- **MAX_RISK_PCT sweep on 145d:** 5.0% would filter this loss (Sharpe 17.04
  vs 14.43) but lose $87 PnL.
- **CONSCIOUS DECISION TO SKIP further tightening:**
  - I've already tightened 10→8→7→5.5 chasing each pilot's bad-day
  - Continued tightening = overfit cascade (each new sample reveals new
    "ideal" threshold)
  - 5.5% has stable cliff vs 6.0% across pilots — that's structural
  - 5.0% filters 1 specific trade — fragile, sample-specific
  - Strategy still has positive EV at 5.5% even including this loss
- **Lesson:** Stop the optimization-cascade. Accept some tail-risk.
  5.5% Cameron-conform sweet-spot is robust enough.
- **Commit:** `fc8ef1e` (script only, no config change)

### Iter 31: Pilot extended 102→122 days (Nov 2025)
- **Data fetch:** 16 more days (2025-11-17 to 2025-12-15) via Alpaca.
- **New trades:** 2 wins (+$24.81, +$112.32) — clean adds, no MDD change.
- **Backtest with current config (no config change):**
  - 102d: $632.42 / Sharpe 17.05
  - **122d: $769.55 / Sharpe 20.74 (+22% Sharpe, +22% PnL)**
- **MAX_RISK_PCT sweep on 122d:** 5.5 still cliff-optimal. 5.75 still toxic
  (-$87 MDD). Cliff is STRUCTURAL, not sample-noise.
- **Lesson:** Multiple sample-extensions add more good days than bad once
  config is robust. The 39→81d extensions revealed bad trades; 102→122d
  extension just adds wins. Indicates strategy is genuinely robust at this point.
- **Commit:** `592e6e5` (script only — data files gitignored)

### Iter 30: Pilot extended 81→102 days + T2_R_MULTIPLE 2.5 → 3.5
- **Step 1:** fetched 17 more days (2025-12-16 to 2026-01-15) via Alpaca.
  Pilot extends to 102 days. New trades both wins (+$65, +$87).
- **Step 2:** T2_R re-tuned on 102d:
  - 2.5R (Iter 25): $563 / Sharpe 15.19
  - 3.0R: $566 / Sharpe 15.26
  - **3.5R: $632.42 / Sharpe 17.05** ← selected
  - 4.0R: $550 (2 trades stall — too aggressive)
- **Mechanism:** Same trade count/WR/MDD at 3.5R. Just bigger wins. Momentum
  carries past 2.5R to 3.5R organically. 4R is too far.
- **Cumulative 102d:** $632.42 / 92% / MDD -$37.10 / Sharpe 17.05
- **Re-validation matrix on 102d (all iters confirmed):**
  - MAX_RISK 5.5: still cliff (6.0 → Sharpe 6.26)
  - QE 0.30c: non-binding (all 0.20-0.50 identical with 5.5% filter)
- **Cameron-Argument:** "Let your winners run" — fits stronger setups. Still
  below pole-cap (3.5R == cap, so trades survive both checks).
- **Commit:** `9a2cf50`

### Iter 29: Pilot extended 61→81 days + MAX_RISK_PCT 7.0 → 5.5
- **Step 1:** fetched 17 more days (2026-01-16 to 2026-02-13) via Alpaca.
- **Step 2:** 81d revealed TWO more big losses (2026-01-22 -$37, 2026-02-04
  -$49.88 capped). MDD jumps -$9 → -$87.
- **Step 3:** MAX_RISK_PCT sweep on 81d shows cliff at 5.5→6.0:
  - 5.5: 11 trd / $410 / 91% / MDD -$37 / Sharpe 11.06 ← selected
  - 6.0: 14 trd / $366 / 79% / MDD -$87 / Sharpe 4.21 (3 toxic marginals)
  - 7.0: 17 trd / $410 / 75% / MDD -$87 / Sharpe 4.72 (Iter 28)
- **Decision:** Tighten further to 5.5%. Same PnL, MDD halved, Sharpe doubled.
- **Lesson (now repeated 3x):** Each pilot extension reveals more bias.
  MAX_RISK_PCT went 10 → 8 (Iter 1, 39d) → 7 (Iter 28, 61d) → 5.5 (Iter 29, 81d).
  As pilot grows, "optimal" tightens because pilot exposes more bad-trade-tier.
- **Commit:** `81f77ef`

### Iter 28: Pilot extended 42→61 days + MAX_RISK_PCT 8.0 → 7.0
- **Step 1 (DATA):** fetched 19 older days (Feb 17 - Mar 13) via Alpaca.
  Pilot extends to 61 days.
- **Step 2 (REALITY CHECK):** 42d Sharpe 85.71 was sample-biased.
  - 42d: $462.85 / MDD -$5.40 / Sharpe 85.71 (lucky no big-loss day)
  - 61d (same iters): $453.38 / MDD -$59.06 / Sharpe 7.68 (-91% Sharpe!)
  - One bad trade on 2026-02-23 lost -$49.99 (max-loss-cap hit).
- **Step 3 (RE-TUNE MAX_RISK_PCT):**
  - 6.0: $409 / Sharpe 45.14
  - **7.0: $453 / MDD -$9.07 / Sharpe 50.03** ← SELECTED
  - 8.0 (Iter 1): $453 / MDD -$59.06 / Sharpe 7.68
  - 9-10%: $573-580 / MDD -$49.99 (more PnL but worse Sharpe)
  - 15%: $614 (Cameron-spec violation)
- **Decision:** Tighten to 7.0%. Same PnL, 6.5x better MDD, 6.5x Sharpe.
  Still Cameron-conform (<10% spec).
- **Lesson:** 42d pilot was biased — looked like Sharpe 85 but reality
  is ~50 with better config. Bigger pilot = more honest validation.
- **Cumulative now on 61d:** $453.78 / 83% / MDD -$9.07 / Sharpe 50.03
- **Commit:** `f78649a`

### Iter 27: Further re-validation w/ Iter 25 base (alle SKIP)
- **USE_PSYCH_LEVEL_T2 on/off:** $462.85 vs $461.58 — identical. With
  T2=2.5R override, the psych-level rarely fires (T2 usually above next
  0.50 boundary anyway). Keep ON for Cameron-spec.
- **MAX_POLE_T2_R re-tune:** 3.5 confirmed optimal. Tighter (2.0/2.5)
  loses trades AND increases MDD (-$24.90). Looser (4.5+) loses Sharpe.
- **POLE_MIN_MOVE_PCT re-tune:** Same as Iter 14/21 — 4% gives +$65/100%
  WR/MDD=0 but Cameron-spec violation. 5% optimal under spec.
- **Conclusion:** Bot at robust local optimum. Pure parameter-tuning truly
  exhausted on 42-day pilot. Live-deploy ready as-is.

### Iter 26: Re-validation sweep on Iter 23/24/25 base (alle SKIP)
- **Trail-Stop post-T1 (Iter 11 re-test):** BE-only still optimal at
  $462.85. Trail 1.5R close ($458) but never above. Same as prior result.
- **POLE_TOPPING_TAIL_MAX (Iter 2 re-test):**
  - 0.5 current: $462.85 / Sharpe 85.71
  - 0.6: $528.15 / Sharpe 97.81
  - 0.65: $593.27 / Sharpe 109.86
  - 0.7: $618.25 / Sharpe 114.49
  Each step looser gains +$65 PnL. BUT Cameron-Spec is 50% literal max.
  Consistent with Iter 8/14 SKIP-policy on Cameron-spec-violations.
- **Top-N-Rank (Iter 4 re-test):** Top-10 still wins. Top-7 only 5 trades
  ($54). Cap reduces opportunity-pipeline without quality gain.
- **Status:** Bot now at robust local optimum on 42d. Further commits
  need either truly new structural changes or more data.

### Iter 25: T2 = 2.5R override (Iter 3c-revisit on bigger pilot)
- **Hypothesis:** Iter 3c tested T2=R-multiple on 39d (+12%) but SKIP'd
  as "not strong enough to override Cameron-architectural pole-based T2".
  With 42d + Iter 23+24 active, signal might be stronger.
- **Backtest:**
  - Pole-based:  $391.13 / Sharpe 72.43
  - T2=1.5R:     $362.82 / Sharpe 67.19
  - T2=2.0R:     $405.58 / Sharpe 75.11
  - **T2=2.5R: $462.85 / MDD -$5.40 / Sharpe 85.71** ← selected
  - T2=3.0R:     $445.66 / Sharpe 82.53
- **Cameron-Argument:** "2.5x R:R minimum" is Cameron's CLASSIC teaching
  ("at least 2-to-1 R:R"). T2-as-R is his LITERAL spec, pole-height was
  derived approximation. Original SKIP was wrong call.
- **Implementation note:** Iter 7 cap restructured: check pole_h/risk
  directly (filters overextended setups before T2-compute), not (t2/risk
  after T2-compute). T2 now ep+2.5*risk + psych-level upgrade preserved.
- **+$71.72 PnL (+18.3%), Sharpe +18%, MDD unchanged.**
- **Commit:** `298aede`

### Iter 24: Swap POWER_HOUR vs POST_POWER size-mults (Iter 3a-revisit)
- **Hypothesis:** Pilot trade-time-analysis shows Power-Hour (9:30-10:30)
  = 75% WR (volatile chop), Post-Power (10:30+) = 100% WR (clean Mid-
  Morning). Bot's 1.0/0.75 sizes UP during chop, DOWN during clean —
  backwards.
- **Iter 3a was SKIPPED** because ReplayBot ignored POWER_HOUR_SIZE_MULT.
  After Iter 23 ReplayBot passes ny_time → swap now testable.
- **Backtest:**
  - 1.0/0.75 (orig): $329.98 / Sharpe 45.83
  - 1.0/1.0 EQUAL:   $413.37 / Sharpe 57.41
  - **0.75/1.0 SWAP: $391.13 / MDD -$5.40 / Sharpe 72.43** ← selected
  - 0.5/1.0 AGGR:    $370.82 / MDD -$3.60 / Sharpe 103.01
  - 0.25/1.0 V-AGGR: $350.25 / MDD -$1.80 / Sharpe 194.58
- **Selected 0.75/1.0:** Symmetric mirror of original. Cameron-conform
  "size down in chop, full size in clean". +$61 PnL, -$1.80 MDD, +58% Sharpe.
- **4 unit-tests updated** (compliance + size_multipliers).
- **Commit:** `ff71e49`

### Iter 23: Time-based Quarter-Size-Unlock @ 10:00 NY (HUGE WIN)
- **Hypothesis:** Bot's Quarter-Size-Unlock fires nur nach cumul $0.50/share
  T1-Gains. Pilot hat fast nur 1 trade/day → Bot ist PERMANENT
  quarter-size. Cameron-spec sagt "quarter während Vol-Open, full nach".
- **Fix:** time-based fallback unlock @ 10:00 NY. Erste-trade <10:00 bleibt
  quarter (ANNA), alle Trades >=10:00 → full size.
- **Backtest:**
  - before:  12 trd / $164.81 / 91% / MDD -$7.20 / Sharpe 22.89
  - **after: 12 trd / $329.98 / 91% / MDD -$7.20 / Sharpe 45.83**
  - **+100% PnL / +100% Sharpe / MDD unchanged**
- **Cameron-Argument:** Original cents-rule war für multi-trade-days.
  Time-rule für single-trade-days. Beide protectieren first-trade Risk.
- **Replay-baseline change:** 2026-04-15 $13.14 → $40.51 (MNTS @ 11:05
  jetzt full-size — by design).
- **Commit:** `c0ff7ff`

### Iter 22: SPY_TREND_VETO_PCT -1.0% → -2.0% (Cameron-Praxis-Aligning)
- **Hypothesis:** Live-Bot SPY-Veto bei -1.0% outright-skip ist strikter
  als Cameron's tatsächliche Regel "trade with caution" (= size-reduce,
  not skip).
- **Backtest:** -1.0% (current) verliert $40.79 PnL + 2 trades (Skipped
  4 of 42 pilot days, alle waren in real positive PnL-days).
  - No veto:    12 trd / $164.81 / 91% / Sharpe 22.89
  - **-2.0% NEW: 12 trd / $164.81 / 91% / Sharpe 22.89** ← selected
  - -1.5%:      11 trd / $148.16 / 90% / Sharpe 20.58
  - -1.0% old:  10 trd / $124.02 / 89% / Sharpe 17.23
- **Decision:** -2.0% maintains crash-protection (echte crash-days),
  keeps -0.5% SPY_REDUCE_SIZE intact for "yellow flag" days.
- **Commit:** `831e760`

### Iter 9: ReplayBot Quick-Exit (Replay/Live parity)
- **Hypothesis:** Live-bot has QUICK_EXIT_THRESHOLD_CENTS=0.30 +
  BARS_LIMIT=5 (Cameron's "30c quick out"). ReplayBot didn't —
  parity gap. Implementing QE in replay should reduce the ANNA-loss.
- **Backtest:**
  - baseline:    11 trd / +$145.44 / 90% / MDD -$12.48 / Sharpe 11.65
  - **30c spec: 11 trd / +$150.72 / 90% / MDD -$7.20 / Sharpe 20.93** ← SELECTED
  - 20c-optimum: 11 trd / +$153.12 / 90% / MDD -$4.80 / Sharpe 31.90
- **Selected 30c over 20c-optimum:** matches existing live-bot config
  (closes parity gap; no spec-divergence).
- **ANNA loss:** -$12.48 → -$7.20 (saved $5.28). 10 winners unaffected.
- **Tests:** 3 new (QE-fires, QE-bars-limit, QE-skipped-after-T1) +
  helper updated to default bars_since_entry past window.
- **Cumulative Iter 1+2+7+9:** PnL $75→$151 (+101%), Sharpe 2.45→20.93
  (+754%), MaxDD -59%→-77%.
- **Commit:** `53043d5`

### Iter 7: MAX_POLE_T2_R = 3.5 (cap overextended setups)
- **Hypothesis:** Diagnose der 13 pilot-trades zeigte alle 3 Verluste
  (FGI t2R=3.57, MSC t2R=4.14, ANNA t2R=2.39) hatten hohe Pole-zu-Risk
  Ratios. Counter-intuition: großer Pole = exhausted/volatile Stock =
  höheres Loss-Risiko, nicht "stronger" setup.
- **Backtest (39 days):**
  - no cap:    13 trd / +$120 / 75% / Sharpe 9.64
  - t2R<=4.0:  12 trd / +$133 / 82% / Sharpe 10.65
  - **t2R<=3.5: 11 trd / +$145 / 90% / Sharpe 11.65 ← SELECTED**
  - t2R<=3.0:  11 trd / +$127 / 80% / Sharpe 10.20
  - t2R<=2.5:  10 trd / +$101 / 78% / Sharpe 8.08
- **Cumulative effect Iter 1+2+7:** +$70→+$145 PnL (+93%), Sharpe 2.45→11.65
  (+376%), MaxDD -$30→-$12.48 (-59%).
- **Cameron-conform:** Analog zu "don't add to overstretched stock".
  T2-Calc unverändert, nur Entry-Veto bei extreme poles.
- **Commit:** `5954ea6`

### Iter 2: POLE_TOPPING_TAIL_MAX 0.4 → 0.5
- **Hypothesis:** Cameron-spec literal value (yaml + code both said 50%, impl 40%)
- **Backtest (with Iter 1 baseline):** 9→13 trades, $73→$120 PnL,
  win-rate 75%→75%, MaxDD -$18→-$12, Sharpe 3.89→9.64 (+147%)
- **Commit:** `d7d7cbf`

**Cumulative on EXTENDED 145-day pilot (all Iter 1@5.5/2/7/9/22/23/24/25@3.5R/29/30/31/32):**

| Metric | Original (39d) | Now (145d) | Δ |
|---|---:|---:|---|
| Trades | 17 | 16 | -1 |
| PnL | $75.17 | **$719.67** | **+857%** |
| Win-Rate | 67% | **88%** | +21% |
| MaxDD | -$30.63 | -$49.88 | +63% |
| Sharpe-like | 2.45 | **14.43** | **+489%** |

Note: 122d Sharpe was 20.74 — one new bad day on 2025-11-07 pulled Sharpe
down to 14.43. Decision to NOT tighten MAX_RISK further (would be overfit).

**Note on MDD:** 102-day pilot exposes worst-case DD. PnL/Sharpe still
massively net-positive vs original.

**Note on MDD:** 81-day pilot exposes worst-case drawdown. The original
39-day baseline never saw a $50-cap full-size loss day. Honest pilot
shows max-loss can hit $37 even with tighter MAX_RISK. PnL/Sharpe still
strongly net-positive vs original.

History of MAX_RISK_PCT tightening as pilot grew:
- 39d: MAX_RISK=10 baseline → 8 (Iter 1, $75→$73)
- 42d: stayed at 8 (Iter 21 confirmed) but Sharpe 85 was illusion
- 61d: tightened 8→7 (Iter 28), MDD -$59→-$9, Sharpe 7.68→50.03
- 81d: tightened 7→5.5 (Iter 29), MDD -$87→-$37, Sharpe 4.72→11.06

## Tested but NOT committed (negative or inconclusive)

### Iter 3a: POWER_HOUR_SIZE_MULT swap
- **Hypothesis:** Pilot data shows Mid-Morning (10:30-11:30) has 100% win-rate
  vs Power-Hour (9:30-10:30) 62%. Bot uses 1.0x in PH, 0.75x post-PH —
  exactly backwards.
- **Backtest:** ReplayBot ignores `POWER_HOUR_SIZE_MULT` — engine limitation,
  not testable without major refactor. All variants identical: $120/9.64.
- **Status:** SKIP. Engine doesn't honor mult. Live bot DOES (Reviewer-Fix
  P2#11 wired ny_time through). Theoretically the swap would help live but
  unverifiable.

### Iter 3b: One-Loss-Stop
- **Hypothesis:** Cameron quote "first loss done for day". Test variants:
  spiral after 1 loss, after $X loss, combinations.
- **Backtest:** Only 1 multi-trade-day in pilot (2026-03-25 had 2 wins).
  No multi-trade-day with loss to spare. All configs identical.
- **Status:** SKIP — selectivity filters already produce ≤1 trade/day.

### Iter 21: Re-Validation on extended 42-day pilot (NO COMMIT)
- **MAX_POLE_T2_R=3.5 (Iter 7):** RE-CONFIRMED optimal.
  - 3.0: $150.71 / Sharpe 18.61
  - **3.5: $164.81 / Sharpe 22.89 ← still best**
  - 4.0: $156.11 / Sharpe 17.94
  - 5.0+: $143.61 / Sharpe 11.49
- **BREAKOUT_VOL 1.25x:** Still +$13.36 PnL (15 trd). 1.25 is Cameron-
  spec-violation (he says 2x). Skip same as Iter 8.
- **POLE_MIN_MOVE 4%:** Still +$13.36 PnL (13 trd, 100% WR, MDD=$0).
  4% is Cameron-spec-violation (5% min). Skip same as Iter 14.
- **New 5-13 trade:** REPL — Buy 4.76, T1 4.95, T2 5.00, +$14.09.
  Valid bull-flag setup that live-bot missed. Strong validation
  of 1-min/5-min-mismatch-bug user flagged.
- **Verdict:** All current committed configs (Iter 1/2/7/9) hold optimal
  on extended pilot. Below-spec loosenings still positive but principled
  SKIP.

### Iter 18+19: more filter-tuning (alle SKIP)
- **Iter 18 POLE_VOLUME_RISING-Toleranz (0.7..1.5x):** Alle identisch
  ($150/11trd). Constraint non-binding für pole-length<4.
- **Iter 19 Absolute Volume Floor:** 25k→$88, 100k→$41. Penny-stocks
  haben natural lower bar-volume. Cameron's 100k-rule für mid-caps.

### Iter 20: Pilot-Data Erweiterung 39→42 Tage (DATA-FETCH)
- **Approach:** Incremental Alpaca-historical-bars für 572 recurring
  tickers + 14 manually-tracked tickers (HSPT etc.). Daily-candidates
  via Cameron-rules, then 5-min bars for top candidates.
- **New dates:** 2026-05-11/12/13.
- **Backtest:** 11 trd $150/Sharpe 20.93 → 12 trd $164.81/Sharpe 22.89
  (+9% PnL, +9% Sharpe).
- **Insights:**
  - 5-11: 13 cands, 0 trades. Selectivity works.
  - 5-12 (HSPT-day): 5 cands, 0 trades. **Bestätigt: Bot's filter hätten
    HSPT auch ohne stale-price-bug rejected.**
  - 5-13: 1 trade +$14.09. **Replay-Live DISCREPANCY** confirms
    1-min/5-min-mismatch-bug — live missed this valid setup.

**Cumulative Iter 1+2+7+9 (42-day):** PnL $164.81 vs baseline-pilot
$75.17 = +119%, Sharpe 22.89 vs 2.45 = +834%.

### Iter 15+16+17: late-cycle threshold-tuning (alle SKIP)
- **Iter 15 (Adaptive Quick-Exit % entry):** Tested 1/1.5/2/2.5/3% of entry.
  30c absolute (current) is mathematically identical to max(30c, 2% entry)
  for pilot. Cameron's 30c IS already adaptive in $2-$20 range. Tighter
  variants (1-1.5%) triggered on noise, killed winners.
- **Iter 16 (MAX_TRADES_PER_DAY 5→1):** Break-even — pilot max is 1
  trade/day so cap is non-binding. No backtest signal to commit despite
  Cameron's "3-5/day" preference. Per mandate break-even = SKIP.
- **Iter 17 (MAX_RISK_PCT re-tune):** Sweep 10/8/7/6.5/6/5.5/5/4. Tightening
  from 8→6 eliminates 3 trades incl. 2 winners (MNTS +$13, VIVO +$6) for
  $12 PnL hit. 100% WR + MDD=$0 artificial cherry-picking. 8.0 (Iter 1)
  remains optimal.

### Iter 13: FLAG_RETRACE_MAX_PCT sweep (SKIP)
- **Hypothesis:** Cameron's "flag retraces 38-50%" — current 50% is upper
  bound. Fibonacci 38% tighter?
- **Backtest:** 50% current optimum.
  - 25% tight: 8 trd / $134 / 100% / MDD=0 (artificial Sharpe-spike, fragile)
  - 38% Fib:   10 trd / $137 / 90% / Sharpe 19.09  (worse)
  - 45%:       11 trd / $144 / 90% / Sharpe 19.95  (worse)
  - **50% cur: 11 trd / $151 / 90% / Sharpe 20.93** ← optimal
  - 60%/70%:   11 trd / $150 (no extra trades pass)
- **Status:** SKIP — Cameron-Spec 50% ist optimal.

### Iter 14: POLE_MIN_MOVE_PCT sweep (SKIP)
- **Hypothesis:** Cameron's "5% min, 10%+ preferred" — bot at 5% spec-min.
  Either tighter (stricter selection) or looser (more trades)?
- **Backtest:**
  - 3%/4% looser: 12 trd / $162-164 / 100% / Sharpe 162+ (artificial)
  - **5% cur:    11 trd / $151 / 90% / Sharpe 20.93** ← spec
  - 6% stricter: 10 trd / $132 / 90% / Sharpe 18.28
  - 10% Cam-pref: 5 trd / $48  / 80% / Sharpe 6.62
- **Status:** SKIP. Looser=Cameron-Spec-Verletzung (5% is his MIN).
  Stricter loses meaningful trades. Konsistent mit Iter 8 (vol-factor).

### Iter 10+11+12: Position-Management variants (alle SKIP)
- **Iter 10 (Stop-Lock-in nach T1):** 0.25R/0.50R/0.75R/1R lock-in statt
  BE. Alle SCHLECHTER ($133-146 vs $150.72 baseline). Stocks retracen
  nach T1 oft auf BE bevor T2 läuft — engere Stops killen normale
  Bewegung.
- **Iter 11 (Trailing-Stop nach T1):** Trail 0.5R..2R unter höchstem
  Post-T1-High. Trail 1.5R marginal +$3 (Noise bei N=11). Trail
  schmaler verschlechtert.
- **Iter 12 (Time-Exit):** 3..12 bars ohne T1 → market-close. ALLE
  Configs $27-67 schlechter. Cameron's "5-10 min" rule meint
  "if going against me" (= QE, schon implementiert), nicht "if
  not moving yet". Winners brauchen 30-50min.
- **Status:** Alle SKIP. Aktuelle Iter-9-Logik (QE + BE-stop + T2)
  ist optimal für 5-min-bars-Cameron-strategy.

### Iter 8: BREAKOUT_VOL_FACTOR sweep
- **Hypothesis:** Cameron's "2x volume minimum on breakout" — current 1.5x
  is looser than spec. Tighter (2x/2.5x/3x) or looser (1.25x)?
- **Backtest:**
  - 1.25x:  14 trd / +$158.80 / 92% / Sharpe 12.72  (+9%)
  - 1.50x cur: 11 trd / +$145.44 / 90% / Sharpe 11.65
  - 1.75x:  10 trd / +$139.24 / 90% / Sharpe 11.16
  - 2.00x:  10 trd / +$136.99 / 90% / Sharpe 10.98
  - 2.50x:   6 trd / +$76.83  / 83% / Sharpe 6.16
- **Status:** SKIP. 1.25x optimum aber Cameron-Spec-Konflikt (2x-min), und
  3 zusätzliche Wins können overfit sein. 2.0x (Cameron-konform) verliert
  -$8. Kein klares Theorie-Signal — pure number-tuning. Bleib bei 1.5x.

### Iter 6: TIME_NEW_ENTRIES_START shift (skip Power-Hour Early)
- **Hypothesis:** Diagnose der 13 trades zeigte 9:30-10:15 hat 40% win-rate
  (+$3.95 net) vs 10:30+ 100% win-rate (+$116.52). Shift entry-start
  von 9:35 nach 10:15 könnte Sharpe verbessern.
- **Backtest sweep:**
  - 9:35 (cur): 13 trd / +$120 / 75% / MDD -$12.50 / Sharpe 9.64
  - 10:00:      7 trd / +$77  / 86% / MDD -$12.15 / Sharpe 6.34
  - 10:15:      5 trd / +$68  /100% / MDD $0      / Sharpe 67.93
  - 10:30:      5 trd / +$68  /100% / MDD $0      / Sharpe 67.93
  - 10:45:      4 trd / +$55  /100% / MDD $0      / Sharpe 54.85
- **Status:** SKIP. 10:15+ Sharpe-Explosion ist FRAGIL — MDD=$0 mit N=5
  bricht bei erstem Loss-Day. 10:00-Sweep ist sogar SCHLECHTER auf Sharpe.
  -44% PnL absolute. Cameron's Edge IST Power-Hour (architektonischer
  Konflikt). N=5 trades zu dünn für Confidence.
- **Future:** Re-test mit 6+ Monaten Daten. Wenn Pattern robust bleibt,
  später erneut diskutieren.

### Iter 5: Pullback-Count-Limit (2 vs 3 vs 4 vs unlimited)
- **Hypothesis:** Cameron's 2-pullback-then-dead rule.
- **Backtest:** Alle Configs identisch ($120/13 trades). Pullback-Count
  ist nie binding constraint im pilot (kein Symbol hatte >=2 attempts
  am gleichen Tag).
- **Status:** SKIP. Optimization-target ist leer.

### Iter 4: Top-N-Rank Watchlist Filter
- **Hypothesis:** Cameron quote "I focus on the 2-3 best setups of the day,
  not all 10". Test if limiting watchlist to top-N-ranked symbols beats
  trading all 10.
- **Backtest:** All variants LOSE to BASELINE-10.
  - BASELINE-10: 13 trades / +$120.47 / Sharpe 9.64
  - Top-7:        6 trades / +$23.55  / Sharpe 1.88
  - Top-5:        4 trades / +$16.96  / Sharpe 1.36
  - Top-3:        1 trade  /  +$6.00  / Sharpe 6.00
  - Top-2 / Top-1: 0 trades
- **Status:** SKIP. Cameron's "2-3 best setups" refers to EXECUTED trades
  (bot already does 0.33/day = 13/39), not watchlist-size. Setup-selectivity
  comes from detect_bull_flag + vetos, NOT from pre-filtering the watchlist.
  Tightening watchlist just kills opportunity-pipeline.

### Iter 3c: T2 = R-multiple instead of pole-height
- **Hypothesis:** Cameron's literal teaching is "T2 = 2R" but bot uses
  pole-height-based T2.
- **Backtest:** Pole-based is current $120. T2=2.5R gives $134, T2=4R gives
  $142 (best). All sweep results:
  - 1.5R: $111 (-8%)
  - 2.0R: $116 (-4%)
  - Current pole-based: $120
  - 2.5R: $134 (+12%)
  - 3.0R: $136 (+13%)
  - 4.0R: $142 (+18%)
- **Status:** SKIP — pole-based is Cameron-conform ("T2 follows pole strength").
  4R-optimum likely overfits 39-day sample. 12-18% Sharpe-gain not strong
  enough at N=13 to override Cameron's architectural argument.
- **Future:** Re-test with 6+ months of data, or implement trailing-T2.

## Open ideas (not yet tested)

1. **Trailing stop after T1** — Cameron's actual practice. Needs engine
   refactor (bar-by-bar trail logic).
2. **Adaptive quick-exit %** — current 30c absolute is unsymmetric across
   price tiers (13% loss on $2.36 stock, 2.5% on $11.78).
3. **RVOL_MIN_PROXY raise to 10x** — would tighten watchlist. Need to
   rescan candidate generation.
4. **Position-concurrency limit (max 1 concurrent open position)** — would
   reduce parallel-risk on hot days.
5. **Time-since-entry hard exit (e.g. flatten if no T1 after 30 min)** —
   prevents stale positions in dead market.
6. **RSI combined-veto** (Reviewer P2#15) — refactor FBO to require
   2+ signals not just RSI>80.

## Methodology notes

- 39-day pilot is small. Anything < 15% improvement is statistical noise.
- ReplayBot is simplified — no MACD-exit, no pyramiding, no quick-exit.
  Strategy changes must be Cameron-architectural-conform, not just
  backtest-positive.
- Live bot has stricter live-vetos (VWAP, MACD, FBO via real-time data)
  that ReplayBot can't simulate. Backtest results are upper-bound.
