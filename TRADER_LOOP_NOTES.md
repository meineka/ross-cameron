# Trader-Loop Notes (running)

Each iteration: hypothesis tested, kept (committed) or rejected (documented).
The committed changes need to remain robust under future-data validation.

## Committed (positive backtest evidence)

### Iter 1: MAX_RISK_PCT = 8.0
- **Hypothesis:** Cameron's "tight stops only" rule (risk%>10% loses 80% of time)
- **Backtest:** 17 trades → 9 trades, $75→$73 PnL, win-rate 67%→78%,
  MaxDD halved ($30→$18), Sharpe +59%
- **Commit:** `cc371fa`

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

**Cumulative effect Iter 1+2+7+9:**

| Metric | Original | Now | Δ |
|---|---:|---:|---|
| Trades | 17 | 11 | -6 |
| PnL | $75.17 | $150.72 | +101% |
| Win-Rate | 67% | 90% | +23% |
| MaxDD | -$30.63 | -$7.20 | -77% |
| Sharpe-like | 2.45 | 20.93 | +754% |

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
