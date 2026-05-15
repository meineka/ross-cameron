# Review-V2 Audit — All ChatGPT Inputs Mapped to Phases

Audit date: 2026-05-15 post-Phase-23. Every ChatGPT answer file in
`99_Claude_Chatgpt/*_answer_chatgpt.md` has been re-read in full and
mapped to the implementing Phase commit(s).

## Coverage matrix

| Answer (`YYYYMMDD_HHMM`) | Themes / Maßnahmen | Closing Phase(s) | Status |
|---|---|---|---|
| `20260514_1252` | P2.x golden scenarios (7), P1.1 premarket scanner | Phase 16, 17 | ✅ |
| `20260514_1436` | Cameron-Compliance baseline | early (pre-trail) | ✅ |
| `20260514_1749` | Phase 8 design review | Phase 8 | ✅ |
| `20260514_1820` | T1 PnL double-counting on partial T2 | Phase 10 | ✅ |
| `20260514_1840` | P0.1 watchdog, P0.2 log-separation, P1.1-1.3 ops | Phase 11, 12, 13, 16 | ✅ |
| `20260514_1905` | P0.1 deployment review (alpaca import path) | Phase 12 | ✅ |
| `20260514_2011` | Operations Runbook + postmortem + `--preflight-only` | Phase 13 | ✅ |
| `20260514_2221` | Heartbeat-aware false-hang + venv launchers | Phase 14 | ✅ |
| `20260514_2236` … `20260515_0737` (12 confirmations) | Repeated 3-item list: P1.1, P2.x, Postmortem polish | Phase 15, 16, 17 | ✅ |
| `20260515_0811` | P1 postmortem ops + reiterated P1.1 + P2.x | Phase 15, 16, 17 | ✅ |
| `20260515_0849` | 5 tasks: test-gates, slow markers, webservice logging, order-lifecycle log, **P0** single-bot gate | Phase 18, 19, 22 | ✅ |
| `20260515_0902` | 3 tasks: test manifest + review-status honesty + 3-gate scheme | Phase 19, 20 | ✅ |
| `20260515_0915` | 4 tasks: fast-gate widening, full-repo-zip consistency, README sync, experiment scripts cleanup | Phase 21 | ✅ |
| `20260515_0927` | 4 tasks: full-suite report, experiments move, not_reviewed prioritize, **structured logging** | Phase 21, 22 | ✅ |

## Implementation summary by Phase

| Phase | Commit | Theme |
|---|---|---|
| 1-7 | various | Initial Review-V2 (P0.1-P0.5, P1.1-P1.8, P2.x FakeBroker) |
| 8 | `0300de2` | ReplayBot through shared executor |
| 9 | `8dd16d0` | Partial-fill semantics in T2/Stop/QE |
| 10 | `e774b57` | T1 PnL booked once across partial fills |
| 11 | `3f5a8de` | Live/Replay log separation (P0.2) |
| 12 | `74c74c5` | Watchdog interpreter + dep preflight (P0.1) |
| 13 | `412fff0` | OPERATIONS_RUNBOOK + no_trade_postmortem + `--preflight-only` |
| 14 | `4209509` | Heartbeat-aware postmortem + venv launchers |
| 15 | `03a3ca5` | Postmortem diagnostic polish (errors-since-restart + pid-pair dedup) |
| 16 | `63ff391` | Real premarket scanner (Alpaca extended-hours bars + reject reasons) |
| 17 | `43a68bf` | FakeBroker golden scenarios + stop-repair + reject-retry (P2.x complete) |
| 18+19 | `e08147b` | Single-bot-process P0 gate + pytest markers (smoke/critical/integration/slow/full) |
| 20 | `fbb791e` | Machine-readable TEST_MANIFEST.md |
| 21 | `b9da3e1` | Fast-gate widening (smoke+critical) + experiment scripts moved |
| 22 | `7eaadd0` | Structured loggers (`market_data_calls.jsonl`, `order_lifecycle.jsonl`) |
| 23 | `4bcdab0` | bot.log scan-reason extraction in postmortem |

## Trader-loop iteration scoreboard

| Iter | Hypothesis | Verdict |
|---|---|---|
| 1 | Earlier entry cutoff (Cameron Power-Hour) | SKIP |
| 2 | `POLE_MIN_MOVE_PCT 5.0 → 4.0` | ✅ COMMIT (+$197 PnL / +34% Sharpe) |
| 3 | RSI extension veto | SKIP |
| 4 | Trailing stop after T1 | SKIP (catastrophic) |
| 5 | `QUICK_EXIT_THRESHOLD 30c → 20c` | ✅ COMMIT (+$15 PnL / -26% DD / +38% Sharpe) |
| 6 | Late-window size reduction | SKIP |
| 7 | Local-optimum verification | SKIP (current config optimal) |

Cumulative impact on 167-day pilot baseline:
- **PnL: $581.82 → $793.90 (+36%)**
- **MaxDD: -$50.25 → -$37.10 (-26%)**
- **Sharpe-like: 11.58 → 21.40 (+85%)**

## Open items (transparent — not regressions)

These were NOT in any ChatGPT answer as direct asks but are noted as
"next sensible work" by the reviewer:

1. **Wire `structured_logger` into production paths** (Phase 22 ships the
   module; integration into `bot.py` / `AlpacaExecutor` is deliberately
   the next phase so loggers can be observed under live shadow before
   becoming load-bearing).

2. **Source-grep tests → behavior tests**: Phase 21 promoted 5 files
   from `not_reviewed` → `partially_reviewed`. Full conversion of the
   remaining source-grep-only tests (manifest flags them) is future work.

3. **TEST_MANIFEST.md `not_reviewed` list**: ~46 of 63 files still
   carry the safer-default `not_reviewed` label. This is honest, not a
   regression — reviewing each line-by-line is incremental work.

## Tests

- Full suite: **652 passed, 1 skipped** (post-Phase-23)
- Fast gate (`smoke or critical`): **92 passed in ~6 s**
- 63 test files, 651 `def test_*` functions, 640+ collected by pytest

## Trade safety status

- ✅ **P0 watchdog deps** (Phase 12)
- ✅ **P0 single-bot uniqueness gate** (Phase 18)
- ✅ **P0 partial-fill state correctness** (Phase 9-10)
- ✅ **P0 broker-state-machine repair** (Phase 17)
- ✅ **P0 live/replay log isolation** (Phase 11)

**Still NOT cleared for live trading with real money.** Operator
gates: morning_check, live_readiness_check, no_trade_postmortem must
all be clean before the next live-day go/no-go decision.
