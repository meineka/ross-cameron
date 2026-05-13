# Cameron-Bot Review Package

This is a paper-trading bot implementing Ross Cameron's bull-flag day-trading
strategy for the US small-cap market via Alpaca's API. The package contains
the full source, test suite, audit findings, backtest data, and open questions.

**Intended audience:** another AI agent reviewing the codebase for:
- Architectural soundness
- Cameron-strategy fidelity
- Bug or robustness gaps not yet caught
- Suggestions for improvement (with backtest-verified justification)

## What this is and isn't

- **Paper trading only** — runs against Alpaca's paper-trading API, no real
  money risk. But code is production-grade because user will eventually flip
  to live.
- **Strategy: Ross Cameron's bull-flag momentum** — premarket scan for small-
  cap movers ($2-$20, +10% intraday, RVOL ≥ 5×, float < 10M, news catalyst),
  then bull-flag pattern detection on 5-min bars with strict vetos.
- **NOT a black-box ML trader** — explicit pattern detection, explicit risk
  rules, explicit position management.

## Package layout

```
REVIEW_PACKAGE/
├── README.md                 # this file
├── CAMERON_RULES.md          # original strategy spec
├── ARCHITECTURE.md           # system design
├── AUDIT_FINDINGS.md         # all bugs found + fixes (35+ iterations)
├── BACKTEST_RESULTS.md       # 39-day pilot replay + config sweep
├── OPEN_QUESTIONS.md         # pending decisions / known limits
├── 06_live_bot/                      # bot source (31 files)
├── tests/                    # test suite (51 files, ~500 tests)
├── config/                   # requirements.txt, Dockerfile, GH-Actions
└── backtest_data/            # pilot 5-min bars (39 days)
```

## Quick numbers

| | |
|---|---|
| Source files | 31 Python modules |
| Test files | 51 (~500 passing tests) |
| Audit iterations | 35+ since 2026-05-12 |
| Backtest period | 2026-03-16 to 2026-05-08 (39 trading days) |
| Backtest result | +$75.17 / 17 trades / 67% win-rate / -$30.62 max DD |
| Current production config | POLE 5%, TOPPING 0.4, RETRACE 50%, VOL 1.5× |

## Running the tests

```bash
cd 06_live_bot
pip install -r ../config/requirements.txt
cd ..
python -m pytest --ignore=tests/test_replay_regression.py --ignore=tests/test_pilot_baseline.py -q
```

(Skip those two because they need network access to Alpaca-API and yfinance.)

## Key entry points to read

1. **`06_live_bot/bot.py`** — main daemon, top-down flow:
   - `premarket_scan()` (line ~270) → universe + 5-pillars filter
   - `detect_bull_flag()` (line ~393) → pattern math
   - `compute_position_size()` (line ~470) → risk-engine
   - `Bot.run()` (line ~1037) → orchestrator
   - `Bot.handle_bar_5min()` (line ~1380) → per-bar decision tree
   - `manage_position()` (line ~1410) → T1/T2/stop/MACD-exit

2. **`tests/test_replay_regression.py`** — known-good baseline test, $13.14 on
   2026-04-15 pilot day after Option-A 5-min-aggregator fix.

3. **`AUDIT_FINDINGS.md`** — every bug class found, severity, fix, regression
   test reference. Read this to understand the failure modes that existed.

## What to look for as a reviewer

1. **Bugs we missed.** Especially around order lifecycle, state-machine
   transitions, race conditions, async/threading.
2. **Cameron-strategy gaps.** Is the bull-flag detection faithful? Are the
   5 pillars correctly enforced?
3. **Edge cases in production scenarios.** Bot has been live-tested on
   paper but issues like 2026-05-11 WS-bug, 2026-05-12 HSPT-stale-price
   bug, 2026-05-13 1-min/5-min mismatch surface only under real conditions.
4. **Test coverage holes.** Where could a future bug land without an
   existing test catching it?
5. **Open questions** — see `OPEN_QUESTIONS.md`. We need a recommendation
   on the threshold-tune trade-offs.

## Code style observations

- All audit-fix comments are tagged `Audit-Iter NN (YYYY-MM-DD) — Bug-Fix XX-N`
  so you can correlate code changes to commit history.
- Defensive coding: try/except boundaries around all I/O + async fences.
- Atomic-write pattern (`os.replace(tmp, final)`) consistently used for
  any JSON state file (status, watchlist, day_summary, delisted_cache,
  universe_cache).
- Persistence files use trading-day-date keys, not system-local-date.
- Cross-platform process management via psutil with wmic/pgrep fallbacks.

## What I (the agent that built this) would want from your review

Concrete, actionable findings — preferably with reproduction steps for any
bug claim. Not generic "you should add more comments" feedback. The user
runs this against real (paper) money daily.
