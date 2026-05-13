# Backtest Results

## Setup

- **Period:** 2026-03-16 to 2026-05-08 (39 trading days)
- **Data:** Alpaca 5-min bars, pilot dataset (`backtest_data/intraday_5m.parquet`)
- **Engine:** `bot.ReplayBot` (simplified subset of live bot — no MACD-exit,
  no pyramiding, no VWAP/FBO vetos, no quick-exit)
- **Account:** $25,000 paper equity (Alpaca-default)
- **Caveat:** Live bot will likely produce ~10-15% fewer trades (stricter live
  filters) but higher quality per trade (~$6-8 vs $4.42 in replay).

## Current Production Config

```python
POLE_MIN_MOVE_PCT       = 5.0     # %
POLE_TOPPING_TAIL_MAX   = 0.4     # ratio
FLAG_RETRACE_MAX_PCT    = 50.0    # %
BREAKOUT_VOL_FACTOR     = 1.5     # x
BAR_AGGREGATION_MINUTES = 5
MAX_LOSS_PER_TRADE_USD  = 50.0
DAILY_GOAL_USD          = 150.0
PRICE_MIN/MAX           = 2.0 / 20.0
RVOL_MIN_PROXY          = 5.0
FLOAT_MAX_SHARES        = 10_000_000
```

## Single-Day Detail (39 days)

```
Date          Trades        PnL       Peak Note
2026-03-16         0 $    0.00 $    0.00  no setup
2026-03-17         1 $  -12.22 $    0.00  loss
2026-03-18         1 $  -12.40 $    0.00  loss
2026-03-19         0 $    0.00 $    0.00  no setup
2026-03-20         0 $    0.00 $    0.00  no setup
2026-03-23         1 $   18.86 $   18.86
2026-03-24         2 $  -24.72 $    0.00  spiral_locked
2026-03-25         1 $    6.20 $    6.20
2026-03-26         0 $    0.00 $    0.00  no setup
2026-03-27         2 $   -6.34 $    6.16  loss
2026-03-30         0 $    0.00 $    0.00  no setup
2026-03-31         0 $    0.00 $    0.00  no setup
2026-04-01         1 $    8.93 $    8.93
2026-04-02         0 $    0.00 $    0.00  no setup
2026-04-06         0 $    0.00 $    0.00  no setup
2026-04-07         0 $    0.00 $    0.00  no setup
2026-04-08         0 $    0.00 $    0.00  no setup
2026-04-09         0 $    0.00 $    0.00  no setup
2026-04-10         1 $   18.44 $   18.44
2026-04-13         0 $    0.00 $    0.00  no setup
2026-04-14         0 $    0.00 $    0.00  no setup
2026-04-15         1 $   13.14 $   13.14
2026-04-16         0 $    0.00 $    0.00  no setup
2026-04-17         0 $    0.00 $    0.00  no setup
2026-04-20         0 $    0.00 $    0.00  no setup
2026-04-21         0 $    0.00 $    0.00  no setup
2026-04-22         0 $    0.00 $    0.00  no setup
2026-04-23         1 $   17.26 $   17.26
2026-04-24         1 $   13.50 $   13.50
2026-04-27         1 $    6.00 $    6.00
2026-04-28         0 $    0.00 $    0.00  no setup
2026-04-29         0 $    0.00 $    0.00  no setup
2026-04-30         1 $   23.52 $   23.52
2026-05-01         1 $  -11.76 $    0.00  loss
2026-05-04         1 $   16.76 $   16.76
2026-05-05         0 $    0.00 $    0.00  no setup
2026-05-06         0 $    0.00 $    0.00  no setup
2026-05-07         0 $    0.00 $    0.00  no setup
2026-05-08         0 $    0.00 $    0.00  no setup
```

## Aggregate (baseline config)

| Metric | Value |
|---|---|
| Days analyzed | 39 |
| Total trades | 17 |
| Total PnL | **+$75.17** |
| Avg PnL/day | +$1.93 |
| Avg PnL/trade | +$4.42 |
| Win days | 10 (26%) |
| Loss days | 5 (13%) |
| No-trade days | 24 (62%) |
| Spiral-locked days | 1 |
| Win rate (decided) | 66.7% |
| Best day | +$23.52 |
| Worst day | -$24.72 |
| Max cumulative drawdown | **-$30.62** |
| Trade-day activity | 38% of days |
| Avg trades / active day | 1.1 |

## Config Sweep (39 days × 10 configs)

Ranked by Total PnL:

| Rank | Config | Trades | PnL | Avg/Trd | Win% | Best | Worst | MaxDD |
|------|--------|------:|----:|--------:|-----:|-----:|------:|------:|
| 1 | all-loose | 27 | **+$200.83** | +$7.44 | **75%** | +$83.93 | -$24.72 | -$24.72 |
| 2 | higher-topping (0.5) | 22 | +$189.84 | **+$8.63** | 71% | +$83.93 | -$24.72 | -$24.72 |
| 2 | looser-pole+top | 22 | +$189.84 | +$8.63 | 71% | +$83.93 | -$24.72 | -$24.72 |
| 4 | all-strict | 12 | +$84.68 | +$7.06 | 73% | +$21.27 | -$12.24 | **-$18.58** |
| 5 | vol-strict (2.0x) | 15 | +$79.12 | +$5.27 | 69% | +$21.27 | -$24.72 | -$31.06 |
| **6** | **BASELINE (production)** | 17 | +$75.17 | +$4.42 | 67% | +$23.52 | -$24.72 | -$30.62 |
| 7 | looser-pole (4%) | 17 | +$75.17 | +$4.42 | 67% | +$23.52 | -$24.72 | -$30.62 |
| 8 | stricter-pole (6%) | 17 | +$75.17 | +$4.42 | 67% | +$23.52 | -$24.72 | -$30.62 |
| 9 | flag-60 | 19 | +$72.80 | +$3.83 | 65% | +$23.52 | -$24.72 | -$24.86 |
| 10 | vol-loose (1.2x) | 19 | +$68.69 | +$3.62 | 69% | +$18.86 | -$24.72 | -$30.62 |

### Key insights from sweep

1. **`POLE_TOPPING_TAIL_MAX 0.4 → 0.5` is the dominant lever** — alone, this
   produces +$114.67 PnL (+153%), more trades (17→22), higher win-rate
   (67%→71%), no extra drawdown. This was the parameter that blocked
   **BWEN today** (topping 0.605 vs threshold 0.4).
2. **`POLE_MIN_MOVE_PCT` variation has NO effect** (4%, 5%, 6% all identical
   results) — the patterns we currently trade have poles >> 6%; the
   threshold is non-binding in this range.
3. **Defensive config (`all-strict`)** wins on risk-adjusted return — lowest
   max-DD ($18.58 vs $30.62 baseline) while still beating baseline on PnL.

## Pending Decision

User asked: should we apply Option B (`higher-topping` 0.4→0.5)?

Pro:
- +$114.67 / +153% PnL on 39-day backtest
- Single-parameter change (low overfitting risk)
- Cameron-conform (Cameron says "topping >50% is concerning" — 0.4 was
  too strict)
- Would have triggered BWEN today (real-time data: 6.98% pole, 0.605 topping,
  passed all other filters)

Contra:
- Sample size 39 days is small
- Live bot has stricter filters than ReplayBot (real impact may differ)

**Status:** awaiting user decision. Code not yet changed.

## Replay-Regression-Test

`tests/test_replay_regression.py::test_replay_2026_04_15_baseline`:
- Asserts MNTS trade on 2026-04-15 produces `Daily realized PnL: $13.14`
- Acts as canary — any code change that breaks T1/T2 accounting,
  pole/flag detection, or order management fails this test

Baseline drift history (documented in test file):
- `$12.15` — initial baseline
- `$10.38` — after 5c slippage + psych-level T2 + 8 easy-wins
- `$7.08`  — after Cameron-strict filters (12.05): VWAP+MACD+FBO+Float+Catalyst+
            Open-range+1%-equity-cap+min-stop+pump-dump-mult
- `$13.14` — after Audit-Iter 19 (Replay-Live PnL parity fix; T1-PnL was
            previously dropped in T2-exit math, gave systematically
            understated baseline)

## Honest assessment

- $75 over 8 weeks on $25k paper = ~0.4% return on capital over 2 months.
  Annualized that's ~2.4% — way below Cameron's claimed live returns.
- Reasons it's so low:
  - Conservative position sizing (1% risk, $50 max-loss cap)
  - Strict filters (5 pillars + VWAP + MACD + FBO)
  - Small sample (39 days, normal Cameron stat sample is 6+ months)
  - Pilot data period may have been a quieter regime
- For comparison, Cameron's own claimed daily target is "$200-500 on $50k
  account" = ~0.5-1% daily. We're at +$1.93/day or 0.008%/day. Two orders
  of magnitude below.
- This is paper trading — when going live, will the bot maintain this edge
  or degrade? Live testing on 2026-05-12 surfaced bugs that didn't show in
  backtest (HSPT stale-price, WS 1-min/5-min mismatch). More live testing
  needed before scaling up.
