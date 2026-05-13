# Trader-Loop Notes (running)

Each iteration: hypothesis tested, kept (committed) or rejected (documented).
The committed changes need to remain robust under future-data validation.

## Committed (positive backtest evidence)

### Iter 1: MAX_RISK_PCT = 8.0
- **Hypothesis:** Cameron's "tight stops only" rule (risk%>10% loses 80% of time)
- **Backtest:** 17 trades → 9 trades, $75→$73 PnL, win-rate 67%→78%,
  MaxDD halved ($30→$18), Sharpe +59%
- **Commit:** `cc371fa`

### Iter 2: POLE_TOPPING_TAIL_MAX 0.4 → 0.5
- **Hypothesis:** Cameron-spec literal value (yaml + code both said 50%, impl 40%)
- **Backtest (with Iter 1 baseline):** 9→13 trades, $73→$120 PnL,
  win-rate 75%→75%, MaxDD -$18→-$12, Sharpe 3.89→9.64 (+147%)
- **Commit:** `d7d7cbf`

**Cumulative effect Iter 1+2:**

| Metric | Original | Now | Δ |
|---|---:|---:|---|
| Trades | 17 | 13 | -4 |
| PnL | $75.17 | $120.47 | +60% |
| Win-Rate | 67% | 75% | +8% |
| MaxDD | -$30.63 | -$12.50 | -59% |
| Sharpe-like | 2.45 | 9.64 | +293% |

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
