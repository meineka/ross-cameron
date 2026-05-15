# Cameron-Bot — AI Handoff Package

**Datum:** 2026-05-15 (post-Phase-21)
**Status:** Paper-trading prototype with broker-state-machine + multi-bot
guard + tiered test gates + experiment-script separation
**Quality:** **~630 tests collected** / **passed** (local-verified, full
suite). Per-file inventory + review-status see `docs/TEST_MANIFEST.md`.

### Export-zip artifact matrix (ChatGPT-09:15 Task 5)

| Artifact | Tests collected | Tests passed | Pilot data | Use case |
|---|---|---|---|---|
| Active local repo (`C:\Users\Szymon\ross-cameron`) | 630 | 629 + 1 skipped | ✅ `04_backtest/data_pilot/*.parquet` | Day-to-day development |
| `*_export_claude.zip` (`AI_HANDOFF_PACKAGE/`) | ~630 | ~629 + 1 skipped | ✅ included | Hand-off to reviewer for full validation |
| `*_full_repo.zip` (entire repo) | ~630 | ~620 + ~10 skipped | ❌ excluded (parquet > 50 MB cap) | Code-only review; replay/pilot tests skip due to missing data |

**Pilot data decision (ChatGPT-09:15 Task 2):** parquet files are
deliberately excluded from `*_full_repo.zip` to keep the zip under
50 MB. Tests gated on `PILOT_DATA.exists()` skip cleanly when run in
that context; the count drift is real and documented above. Reviewers
who need full replay coverage should use the `*_export_claude.zip`
flavor or fetch the parquet via `04_backtest/bootstrap.py`.

⚠️ **NOT cleared for live trading with real money.**

P2.x is **SUBSTANTIALLY COMPLETE** as of Phase 17:
- ✅ FakeBroker module + 7 golden-scenario parity tests on Bot.manage_position
- ✅ Phase 8-11: ReplayBot routes entries + exits through the SAME
  FakeBroker / AlpacaExecutor order-lifecycle as live Bot.
- ✅ Phase 17: dropped-stop repair, exit-rejected fallback,
  stale-quote rejection, BE-stop after T1 — all 7 Golden Scenarios
  (from ChatGPT-12:52) now covered.

P0 + P1 gates after Phase 18-20:
- ✅ Phase 18 (P0): single-bot-process classifier blocks restarts on
  `multiple_independent_bots`. Watchdog refuses spawn.
- ✅ Phase 19 (P1): pytest markers `smoke`/`critical`/`integration`/
  `slow`/`full`. `run_quality_gates.py --fast` runs critical only
  (~35 tests, ~3 s) — Claude-loop friendly.
- ✅ Phase 20 (P1): machine-readable test manifest at
  `docs/TEST_MANIFEST.md` with per-file count / category / review-
  status / source-grep detection.

Open work (P1, no P0 blockers):
- #3 from ChatGPT-08:49: structured per-call logging for yfinance /
  Alpaca / preflight (status, error-class, latency).
- #4 from ChatGPT-08:49: dedicated `order_lifecycle.jsonl` with
  intent → submitted → accepted/rejected → filled/partial → protection.

## Was ist das?

Ross-Cameron-Bull-Flag-Strategy als Python Bot auf Alpaca-Paper-Account (~$25k).
Optimiert über 37 Trader-Loop-Iterationen mit kontinuierlicher Backtest-Validation.

## Review-V2 Status (External-AI-Audit Items Addressed)

External reviewer-V2 audited the package and identified P0 live-blockers
and P1 strategy-fidelity issues. After 4 fix-phases the status is:

### ✅ DONE (13 of 15 items)

**All 5 P0 live-blockers:**
- P0.1 Exit-Orders now poll fills (`submit_sell_with_confirm`) — no more
  "submitted = filled" confusion. Includes market-fallback for stop-exits.
- P0.2 Pyramid-Adds now poll fills (`submit_buy_with_confirm`) — no more
  avg-price drift from unfilled adds.
- P0.3 `verify_and_repair_protection` now ACTIVELY called after every entry-
  fill. Was dead code in V1.
- P0.4 `safe_bracket.check_liquidity` now wired pre-entry — validates two-
  sided quote + spread + volume BEFORE submit. Prevents HSPT-style
  stale-trade-price disasters at root.
- P0.5 `can_enter_new` now aggregates realized + open + new trade risk
  against `DAILY_MAX_LOSS_USD`. Multiple concurrent positions can no
  longer blow through the daily cap.

**7 of 8 P1 strategy-fidelity:**
- P1.2 `two_source_scan` now WIRED — yfinance >20% degraded → query
  Alpaca for missing symbols, recovered symbols added as candidates,
  only truly-missing-in-both-sources marked delisted.
- P1.3 Catalyst-filter: explicit `mode=off|soft|strict`. Strict fails-closed
  on empty/error (previously was permissive — defeated CATALYST_REQUIRED=True).
- P1.4 Float-filter: same 3-mode API. Strict blocks unknown.
- P1.5 SPY-trend intraday refresh — was set once at session-start.
- P1.6 `RESCAN_FAST_PHASE_END` now enforced (was defined-but-unused).
- P1.7 `detect_bull_flag` candidate-local rejects use `continue` (try other
  pole/flag configs) instead of `return False`. Global vetos still return.
- P1.8 Rejection counters (`patterns_rejected_macd/fbo/vwap/risk/...`)
  now actually incremented. Day-summary log prints all 9 categories.

### ⏳ NOT YET DONE

- **P1.1 real premarket scanner** — Bot still uses yfinance daily-bars
  as a Cameron-Pillar-4 proxy. A true premarket-scanner using Alpaca's
  extended-hours bars + gap_pct computation is a major refactor and not
  done. Mitigation: Two-source-scan integration (P1.2) catches yfinance
  outages, and pre-entry quote-safety (P0.4) catches stale-price disasters.

- **P2.x FakeBroker for replay/live parity** — replay still uses
  simplified `ReplayBot._manage` rather than the full live order-state
  lifecycle. The strategy decisions are the same, but the broker mechanics
  diverge. Building a FakeBroker that both live and replay drive through
  would give golden-scenario parity tests.

**Bottom line:** Phase 1-4 substantially closed the gap. Most-dangerous
"intern flat ≠ broker flat" bugs are fixed. Reviewer's pre-condition for
live-money use ("broker-state-machine + FakeBroker tests") is partially
met. Bot is RECOMMENDED for paper-trading and supervised small-capital
live testing. NOT recommended for autonomous overnight unsupervised live.

## Aktueller Stand (Backtest 167 Tage = ~8 Monate)

| Metric | Value |
|---|---|
| Trades | 17 |
| PnL | $581.82 |
| Win-Rate | 81% |
| MaxDD | -$50.25 |
| Sharpe-like (PnL/MDD) | 11.58 |

vs Original (39-day baseline):
| Metric | Original | Now | Δ |
|---|---:|---:|---|
| PnL | $75.17 | $581.82 | +674% |
| Win-Rate | 67% | 81% | +14% |
| Sharpe | 2.45 | 11.58 | +373% |

## Wichtigste Konfig-Werte (in `06_live_bot/bot.py`)

| Param | Value | Iter | Cameron-Spec |
|---|---|---|---|
| `MAX_RISK_PCT` | 5.0 | Iter 36 | <10% (well within) |
| `POLE_TOPPING_TAIL_MAX` | 0.5 | Iter 2 | 50% literal |
| `MAX_POLE_T2_R` | 3.5 | Iter 7 | own "don't chase" filter |
| `T2_R_MULTIPLE` | 3.5 | Iter 30 | "let winners run" 2-2.5+ |
| `QUICK_EXIT_THRESHOLD_CENTS` | 0.30 | (preserved) | "30c quick out" |
| `QUARTER_SIZE_TIME_UNLOCK` | 10:00 NY | Iter 23 | "after volatile open" |
| `POWER_HOUR_SIZE_MULT` | 0.75 | Iter 24 | smaller in chop |
| `POST_POWER_SIZE_MULT` | 1.0 | Iter 24 | full in clean |
| `SPY_TREND_VETO_PCT` | -2.0 | Iter 22 | crash protection |

## Verzeichnis-Struktur

```
AI_HANDOFF_PACKAGE/
├── README.md                          ← this file
├── docs/
│   └── TRADER_LOOP_NOTES.md           ← FULL decision audit trail (37 iters)
├── 06_live_bot/
│   ├── bot.py                         ← MAIN bot file (~2000 lines)
│   ├── catalyst_filter.py             ← yfinance news filter
│   ├── float_filter.py                ← float<10M filter
│   ├── indicators.py                  ← VWAP, MACD, RSI, ATR
│   ├── pump_dump_filter.py            ← PD-multiplier
│   ├── safe_bracket.py                ← bracket order safety
│   ├── bar_aggregator.py              ← 1min → 5min
│   ├── pre_flight.py                  ← startup health
│   ├── deploy_safe.py                 ← prod deployment
│   ├── fetch_*.py                     ← pilot data fetchers (Sep-Nov-Dec-Jan-...)
│   ├── test_*.py                      ← backtest sweep scripts
│   └── diag_trades.py                 ← trade analyzer
├── tests/                             ← 558+ pytest tests (run via pytest tests/)
├── 04_backtest/
│   ├── bootstrap.py                   ← original pilot generator
│   └── data_pilot/
│       ├── intraday_5m.parquet        ← 167-day 5min bars (~895k rows)
│       └── candidates.parquet         ← daily candidate list (6500+ rows)
├── constraints.yaml                   ← strategy constraints
└── requirements.txt                   ← Python deps
```

## How to Run / Reproduce

```bash
# Install dependencies
pip install -r requirements.txt

# Backtest single day
python 06_live_bot/bot.py --replay 2026-04-15

# Full 167-day backtest
python -c "
import sys, logging
from pathlib import Path
sys.path.insert(0, '06_live_bot')
import pandas as pd
import bot
bot.log.setLevel(logging.ERROR)
bars_path, _ = bot.find_pilot_data_paths()
bars = pd.read_parquet(bars_path)
tc = next(c for c in bars.columns if 'time' in c.lower() or 'date' in c.lower())
bars[tc] = pd.to_datetime(bars[tc], utc=True)
dates = sorted(bars[tc].dt.tz_convert('America/New_York').dt.date.unique())
total=0
for d in dates:
    rb=bot.ReplayBot()
    try: rb.run(d.isoformat())
    except: continue
    total += round(rb.day.realized_pnl, 2)
print(f'Total PnL across {len(dates)} days: \${total:.2f}')
"

# Run quality gates (current count: pytest tests/)
python -m pytest tests/ -q

# Live deployment (needs APCA_API_KEY_ID + APCA_API_SECRET_KEY)
python 06_live_bot/bot.py
```

## Key Iterations (siehe TRADER_LOOP_NOTES.md für Details)

**14 Strategy-Commits:**

1. **Iter 1**: MAX_RISK_PCT = 5.0 (started at 10, tightened through 8→7→5.5→5.0 as pilot grew 39→167d)
2. **Iter 2**: POLE_TOPPING_TAIL_MAX = 0.5 (Cameron-literal)
3. **Iter 7**: MAX_POLE_T2_R = 3.5 (filter überextended setups)
4. **Iter 9**: ReplayBot Quick-Exit Parity (Live-Bot hatte schon QE)
5. **Iter 20**: +3 pilot days (Mar-May 2026)
6. **Iter 22**: SPY_TREND_VETO_PCT -1% → -2% (Cameron "caution not skip")
7. **Iter 23**: Time-based Quarter-Size-Unlock @ 10:00 NY (HUGE WIN — 4x sizing post-vol-open)
8. **Iter 24**: Swap POWER_HOUR vs POST_POWER (full size after chop, not during)
9. **Iter 25**: T2 = 2.5R (then upgraded to 3.5R in Iter 30)
10. **Iter 28**: MAX_RISK 8→7
11. **Iter 29**: MAX_RISK 7→5.5
12. **Iter 30**: T2_R 2.5→3.5 (let winners run)
13. **Iter 31**: +20 pilot days (Nov 2025)
14. **Iter 32**: +17 pilot days (Oct 2025)
15. **Iter 35**: +16 pilot days (Sep 2025) — REALITY CHECK exposed Sept-loss-cluster
16. **Iter 36**: MAX_RISK 5.5→5.0 (Sept-data made 5.0 robustly optimal)

**25 SKIPS** sauber dokumentiert mit Begründung (siehe TRADER_LOOP_NOTES.md).

## Bekannte Tail-Risks

- **~$50 max-loss-per-trade** (MAX_LOSS_PER_TRADE_USD = $50)
- **~1 max-cap-loss alle 17 Trading-Tage** in pilot data
- **Sept 2025 zeigte 3-loss-cluster** — strategy hat ~12-15% intrinsic failure rate
- **Real Sharpe range: 6-17** across pilot subsamples (167d says 11.58)

## Live-Lessons aus Paper-Trading

- **2026-05-12 HSPT stale-price bug**: gefixt via `verify_and_repair_protection()` (Audit-Iter 19) + Replay shows filters would have rejected HSPT anyway
- **2026-05-13 1-min/5-min mismatch**: Live-bot fand 0 trades, Replay fand 1 valid trade (REPL +$14). Bug ist isoliert auf bar-aggregation-timing.

## Was die nächste AI tun könnte

**P1/P2 Items aus Review-V2 die noch offen sind (priority):**

1. **P1.1 Real premarket scanner** — Alpaca extended-hours bars, true
   premarket gap/RVOL. The current yfinance-daily-bar proxy is a known
   limitation. Reviewer's spec in commit `2d0c7fe` message.

2. **P2.x FakeBroker** — Order-lifecycle-aware fake-broker that BOTH
   the live Bot AND ReplayBot drive through. Builds golden-scenario
   parity tests (clean win, stop-out, MACD-exit, add-partial-fill,
   exit-rejected, missing-stop-repair). Until this exists, ReplayBot
   results have a known-limitations caveat.

3. **Trailing-Stop nach T1** (Cameron's actual practice — engine-refactor
   needed; tested in Iter 11, sample-noise on 42-day pilot; might be
   different on 167-day with FakeBroker simulating correctly).

4. **6+ Monate mehr Daten** via Alpaca historical (Aug 2025 zurück) — more
   sample for robust validation.

**Was NICHT tun:**

- **Pure Parameter-Tuning ist erschöpft** (25 SKIPs zeigen das).
- **Cameron-Spec-Violations** (Iter 8 vol-factor 1.25x, Iter 14 pole-min 4%,
  Iter 26 pole-topping >0.5) — verlockend backtest-positive aber Overfit.
- **Pilot expand ohne Architektur** — diminishing returns. Multi-month
  data won't fix order-state-machine bugs.
- **"Production-ready" claims ohne FakeBroker tests** — reviewer's main
  concern bleibt valide bis P2.x done.

## Decision-Audit-Trail

Alle 37 Iterationen sind in `docs/TRADER_LOOP_NOTES.md` dokumentiert mit:
- Hypothese
- Backtest-Resultat
- Verdict (COMMIT / SKIP)
- Begründung

Die andere AI kann diese benutzen um:
- Frühere Entscheidungen nachzuvollziehen
- Cameron-Konformitäts-Argumente zu verstehen
- Sample-Bias-Patterns zu erkennen
- Den Trader-Decision-Style zu emulieren
