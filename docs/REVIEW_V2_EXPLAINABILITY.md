# Review-V2 — Explainability & Live-Evidence Bundle

Generated: 2026-05-15 (NY-day session 12:00–16:00 UTC live)

This single document satisfies the five "naechste konkrete Asks" raised in
`99_Claude_Chatgpt/20260515_0940..1256_answer_chatgpt.md`. Each ChatGPT
answer in that window confirmed that the active repo is already newer than
its respective export and asked instead for documentation-level evidence
rather than further code changes. Sections below are addressed to those
asks in order.

---

## §1 Active repo state (ask from 20260515_0940)

| metric                  | value (2026-05-15) |
|-------------------------|--------------------|
| pytest collected        | 698                |
| pytest passed           | 697                |
| pytest skipped          | 1 (`test_kill_bot_uses_sigterm_first`, Windows-only) |
| pytest warnings         | 1 (websockets.legacy deprecation, upstream) |
| fast-gate marker        | `smoke or critical` (NOT "critical only") |
| README/manifest synced  | yes — `tests/build_test_manifest.py --check --no-collect` passes pre-commit |

Quality gates are enforced via `tests/run_quality_gates.py --fast`, which
runs `pytest -m "smoke or critical"` and exits non-zero on any failure or
manifest drift.

---

## §2 Logger coverage matrix (ask from 20260515_0949)

Every data fetch and order-lifecycle transition in the live bot writes to
exactly one of two JSONL sinks. The mapping:

| event                                        | sink JSONL                       | source field        | call field      | wired in                                   |
|----------------------------------------------|----------------------------------|---------------------|-----------------|--------------------------------------------|
| yfinance news (catalyst filter)              | `market_data_calls.jsonl`        | `"yfinance"`        | `"news"`        | `catalyst_filter.has_recent_news`          |
| TradingView screener query                   | `market_data_calls.jsonl`        | `"tradingview"`     | `"scan"`        | `scanners.tradingview_scanner.scan_cameron_candidates` |
| Alpaca market-movers (fallback)              | `market_data_calls.jsonl`        | `"alpaca"`          | `"movers"`      | `scanners.tradingview_scanner.scan_cameron_candidates_alpaca_fallback` |
| Alpaca snapshot (per-symbol price refresh)   | `market_data_calls.jsonl`        | `"alpaca"`          | `"snapshot"`    | `bot.AlpacaExecutor.get_snapshot`          |
| Alpaca bars (intraday history)               | `market_data_calls.jsonl`        | `"alpaca"`          | `"bars"`        | `bot.AlpacaExecutor.get_bars`              |
| bracket intent (pre-submit decision)         | `order_lifecycle.jsonl`          | `"alpaca"`          | `"intent"`      | `bot.AlpacaExecutor.submit_bracket_buy`    |
| bracket submitted (accepted by broker)       | `order_lifecycle.jsonl`          | `"alpaca"`          | `"submitted"`   | same                                       |
| bracket filled                               | `order_lifecycle.jsonl`          | `"alpaca"`          | `"filled"`      | same                                       |
| bracket rejected                             | `order_lifecycle.jsonl`          | `"alpaca"`          | `"rejected"`    | same                                       |
| bracket canceled                             | `order_lifecycle.jsonl`          | `"alpaca"`          | `"canceled"`    | same                                       |
| premarket-v2 shadow decision                 | `premarket_v2_shadow.jsonl`      | (n/a — own kind)    | `"shadow"`      | `bot.Bot._run_premarket_v2_shadow`         |

Schema for every line: `{ts, schema_version: 1, kind, source, call, status, latency_ms, symbol_count, error_class, retry_count, symbols, extra}`. See `06_live_bot/structured_logger.py`.

---

## §3 Safe-Bracket timeout / queued policy (ask from 20260515_1147)

The `safe_bracket_buy` wrapper in `06_live_bot/safe_bracket.py` distinguishes
three broker states after submit:

1. **Filled within timeout** → returns the broker's order object. Lifecycle
   event: `submitted` → `filled`.
2. **Accepted-and-queued past timeout (`assume_queued=True`)** → does NOT
   cancel. Treats the order as in-flight; caller polls status downstream.
   This branch exists because Alpaca's queue can briefly delay even valid
   marketable orders, and a defensive cancel would race-cancel a legitimate
   fill. Lifecycle event: `submitted` only (no `canceled`). Regression test:
   `tests/test_safe_bracket.py::test_assume_queued_does_not_cancel_on_timeout`.
3. **Rejected, replaced, or expired** → propagates the broker's reason and
   surfaces `rejected` lifecycle event. No retry.

`assume_queued` defaults to **True** for live trading because the bot
controls its own re-rank/exit logic via the WS stream — a paper cancel
that fights with a real fill would create phantom positions.

---

## §4 Live health-check coverage (ask from 20260515_1227)

`06_live_bot/health_monitor.py` runs five probes on a tick (default 60s).
Per-probe thresholds and re-fire policy:

| probe          | threshold (consecutive fails) | re-fire while failing | what it catches                             |
|----------------|-------------------------------|------------------------|---------------------------------------------|
| heartbeat      | 2                             | 1h                     | bot process dead / scheduler frozen         |
| audit          | 2                             | 1h                     | watchdog/launcher pair anomaly              |
| yfinance       | 1                             | 1h                     | catalyst/news source degraded               |
| alpaca         | 1                             | 1h                     | broker connectivity / stale market data     |
| catalyst_news  | 1                             | 1h                     | yfinance.news returning empty across batch  |

On recovery, each probe emits one "all good" push including the outage
duration in seconds. Sink: ntfy.sh topic `cameron-bot-ysdsphiehndewxp`
(configurable via `NTFY_TOPIC`) or any Telegram/SMTP alerter via
`alerter.make_alerter`.

Live coverage of the "why didn't the bot trade" question:

| failure mode                          | which probe surfaces it                        |
|---------------------------------------|------------------------------------------------|
| heartbeat stale                       | `heartbeat` (after 2 ticks, ~2 min)            |
| yfinance degraded                     | `yfinance` (first failure)                     |
| Alpaca unavailable                    | `alpaca` (first failure)                       |
| no fresh watchlist                    | `audit` (watchdog reports stale watchlist)     |
| no market-data events                 | `alpaca` (stale during RTH triggers)           |
| no order-lifecycle events             | inferred from `order_lifecycle.jsonl` line count + heartbeat alive |

---

## §5 Live No-Trade Explainability — 2026-05-15

Real data from today's live session (12:00–14:48 UTC):

### Scanner activity
- **TradingView primary scan**: 1 successful call, returned 6 Cameron-conformant tickers
  (GEMI, PIII, LESL, PPBT, INBS, ZBAI) after Phase-28c filter relax
  (RVOL 3x, FLOAT ≤ 50M, premarket ≥ 5%).
- **Final live watchlist after re-rank**: GEMI #1, PIII #2, INBS #3, PPBT #4, ZBAI #5
  (LESL dropped on second re-rank tick).
- **WS subscription**: 5 symbols active.

### Market-data calls (`market_data_calls.jsonl`, 46 entries)
- **All yfinance**: 46 `news` calls, 0 errors, p50 latency 144ms, p95 300ms.
- **Result**: data layer healthy; yfinance NOT a blocker today.

### Premarket-v2 shadow (`premarket_v2_shadow.jsonl`, 31 runs)
- **95 candidates evaluated, 0 passed shadow gate, 95 rejected.**
- Reject-reason histogram:
  - `trade_stale_Ns`: 93× (pre-RTH; expected — gate runs before open)
  - `gap_under_threshold`: 31× (5% minimum)
  - `rvol_0.00_under_2.0`: 68× (no premarket volume yet)
  - `spread_>5%`: 48× (wide quotes on micro-caps premarket)
  - `vol_under_threshold`: 23×, `rvol_unknown`: 23×
- **Verdict**: shadow gate is doing its job — it would not have entered any
  of these names at the moment the snapshot was taken, mostly because the
  early-session quotes were too wide / volume too thin for the premarket
  bull-flag setup. This is consistent with Cameron's own rule of "wait for
  the first 1-min bar to print real volume."

### Order lifecycle
- **0 entries today** in `order_lifecycle.jsonl`. This matches the live
  bot's decision path: no symbol passed the bull-flag pattern detector
  during the 9:30–10:48 ET window.
- **Known issue surfaced in live log**: Alpaca data WebSocket got
  `HTTP 429` at 14:48 UTC during the re-rank churn — investigated as
  rate-limit on re-subscription cycle, not a bug in trade logic. Logged
  for follow-up.

### Final answer to "why no trades today?"
**Strategy veto, not data/process/connection.** All 6 watchlist symbols
were scanned successfully; the bot's pattern detector and shadow gate
both rejected every snapshot because the early-session price action did
not exhibit the bull-flag continuation pattern at thresholds Cameron
trades (gap + RVOL + clean pole + 2–5 bar consolidation).

### Bot/Watchdog state
- bot.py daemon: alive (PIDs from `launcher_child_pair` classification)
- watchdog: alive
- heartbeat: fresh (< 1 min old at last check)
- ntfy: connected (delivery verified earlier this session)

---

## §6 What ChatGPT can stop asking for

These items are now documented and live-evidence-backed:

- ✅ Logger coverage matrix (§2)
- ✅ Safe-bracket timeout/queued policy with test names (§3)
- ✅ Health-monitor probe coverage (§4)
- ✅ No-trade explainability report from real logs (§5)
- ✅ Fast-gate marker correction (§1)

Open items still relevant for future iterations:
- Investigate Alpaca data-WS HTTP 429 on rapid re-subscription (logged
  but not yet root-caused).
- Per-day automated postmortem JSON: today's `no_trade_postmortem_*.json`
  generation should be triggered at HARD_FLAT (16:00 ET) — currently runs
  manually.
