# Trader-Loop Notes (running)

Each iteration: hypothesis tested, kept (committed) or rejected (documented).
The committed changes need to remain robust under future-data validation.

## Committed (positive backtest evidence)

### Iter 1: MAX_RISK_PCT = 8.0
- **Hypothesis:** Cameron's "tight stops only" rule (risk%>10% loses 80% of time)
- **Backtest:** 17 trades → 9 trades, $75→$73 PnL, win-rate 67%→78%,
  MaxDD halved ($30→$18), Sharpe +59%
- **Commit:** `cc371fa`

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

**Cumulative effect Iter 1+2+7+9 (42-day pilot post-Iter 20):**

| Metric | Original (39d) | Now (42d) | Δ |
|---|---:|---:|---|
| Trades | 17 | 12 | -5 |
| PnL | $75.17 | $164.81 | +119% |
| Win-Rate | 67% | 91% | +24% |
| MaxDD | -$30.63 | -$7.20 | -77% |
| Sharpe-like | 2.45 | 22.89 | +834% |

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
