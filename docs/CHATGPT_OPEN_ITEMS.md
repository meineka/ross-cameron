# ChatGPT Review — Open Items (Stand 2026-05-18 20:40)

Konsolidiert aus 42 Answer-Files in `99_Claude_Chatgpt/`. Letzte verarbeitet:
`20260517_2233_answer_chatgpt.md`.

## ✅ Bereits umgesetzt (Phase-22 bis Phase-70)

| ChatGPT-Forderung | Phase | Commit |
|-------------------|-------|--------|
| Structured Loggers (`market_data_calls.jsonl` + `order_lifecycle.jsonl`) | 22, 26 | early |
| Health-Monitor + Alerter (ntfy/Telegram/SMTP/Log) | 25 | early |
| Trade-Push Notifications | 30 | early |
| WebSocket Reconnect-Backoff Patch | 31 | early |
| Scanner Cascade-Storm Fix (`aligned_scan_start`) | 32 | early |
| `bot_ws` Health-Probe | 34 | early |
| RateGuard 200/min + 5s stall-probe | 35 | early |
| Provider-Explicit Alert Titles (`ALPACA STALLED` / `YAHOO OK`) | 36 | early |
| Startup-Push `[INFO] Bot started` | 37, 40, 44 | early |
| OCO/Bracket complex-orders | 46 | early |
| Watchdog-blocked auto-push (30-min re-fire) | 54 | e0eac8d |
| `GuardedTradingClient` + `GuardedStockHistoricalDataClient` | 53 | 05b0899 |
| Fail-CLOSED guard (block_until_allowed=False → no SDK call) | 55 | 957c55f |
| `alpaca_api_calls.jsonl` mit `rate_per_min`, `blocked_ms`, `status=blocked` | 56 | 957c55f |
| status.json mit Rate-Diagnostics-Feldern | 56, 8b23923 | 957c55f + 8b23923 |
| Side-Module guarded (health_monitor / pre_flight / audit / historical_loader) | 57 | 957c55f |
| scanners/ in AI_HANDOFF_PACKAGE | 58 | 957c55f |
| Storm-Regression Test (205 calls) | 59 | 957c55f |
| HARD_FLAT auto-postmortem at 12:00 NY | 60 | 34c8ac3 |
| README.md sync | 60 | 34c8ac3 |
| `last_no_trade_reason` populated at VWAP/MACD/FBO/Risk/Pole/Pullback veto sites | 60 | 34c8ac3 |
| Status-transition Pushes (rate-limited / recovered) | 60 | 34c8ac3 |
| Quality-gates Python preflight warning | 60 | 34c8ac3 |
| HTTP 429 + WS reconnect-throttle explicit regression tests | 60 | 34c8ac3 |
| Active debounce in state-transition push (was dead variable) | 61 | 6c47e63 |
| HSPT stale-quote freshness gate (`MAX_QUOTE_AGE_SEC = 10s`) | 61 | 6c47e63 |
| Hard venv-block in quality_gates | 61 | 6c47e63 |
| PID-Lockfile (single bot.py instance via `bot.pid`) | 62 | 87b72c7 |
| TV scan-status structured logging | 62 | 87b72c7 |
| Historical range fetcher (`fetch_historical_range.py` 1m/5m) | 62 | 87b72c7 |
| Float-cache (Finviz primary + yfinance fallback) | 63 | d1314fc |
| Periodic fetch loop (`fetch_loop.py` every 20min) | 64 | b115e4a |
| Race-safe atomic lockfile (O_EXCL) + Win32 alive-check | 65 | d9fa339 |
| STRATEGY_VARIANT={strict,relaxed,loose} env var | 66, 69 | 6005f62 + c8b5862 |
| .env-load at module-import (persistent variant) | 66.1 | eb8ead8 |
| Supervisor.py (auto-correct meta-watchdog every 30min) | 67 | b41ced4 |
| WS `await stop_ws()` fix (Linux-CI never-awaited bug) | 68 | 17ca2db |
| SKIP_HARD_FLAT_TODAY env-var (afternoon-trading override) | 70 | f23ecb9 |
| **Operator-diagnostic fields in status.json** (alpaca_blocked_count, scanner_source, fallback_used) | **(today)** | **8b23923** |

## ⚠️ Noch offen — verbleibende P1 aus 20260517_2233

| # | Item | Status | Aufwand |
|---|------|--------|---------|
| 1 | Export/repo consistency | ✅ JUST DONE | — |
| 2 | This file (CHATGPT_OPEN_ITEMS.md) update | ✅ JUST DONE | — |
| 3 | status_dashboard.py final consolidation | ✅ JUST DONE (8b23923) | — |
| 4 | `last_no_trade_reason` in ALL skip/veto paths | ⚠️ partial (7/10 sites) | 30 min |
| 5 | Raw-Alpaca-Client classification table | ❌ TODO | 1h |
| 6 | `force_trade_loop.py` paper-demo safety banner | ❌ TODO | 30 min |
| 7 | HTTP-429 explicit test (P2) | ❌ TODO | 1h |

## 🟢 Kritische Blocker — ALLE GESCHLOSSEN

ChatGPT 20260515_2048-Block:
1. ✅ P0: Guard fail-open → Phase-55
2. ✅ P0/P1: Side-modules nicht guarded → Phase-57
3. ✅ P1: Status/Logs für No-Trade-Debug → Phase-56 + 60 + 8b23923
4. ✅ P1: scanners/ im Export → Phase-58
5. ✅ P1: Startup-Storm-Regression-Test → Phase-55 + 60

ChatGPT 20260517_2233-Block:
1. ✅ Export/repo consistency → today
2. ✅ docs update → this file
3. ✅ status_dashboard final → 8b23923
4. ⚠️ last_no_trade_reason coverage → Phase-71 in progress
5. ❌ Raw-Alpaca classification → Phase-71 will add
6. ❌ force_trade_loop banner → Phase-71 will add
7. ❌ HTTP-429 test → backlog (P2)

## Live-Bot Status 2026-05-18 20:40

- **Bot-daemon**: PID 24484, alive seit 20:26:25, **loose-algo + SKIP_HARD_FLAT**
- **Watchdog**: PID 26092, alive seit 20:26:16
- **fetch_loop**: PID 20580, alive seit 20:12:03, 495/1600 universe processed
- **supervisor**: PID 1996, alive seit 20:12:04, 30-min auto-correct cycles
- **Tests**: 922 passed (full --slow gate)
- **Trading window today**: 20:26 → 21:55 Berlin (~1.5h remaining)
- **Equity**: $99,988 paper

## Phase-Summary 2026-05-13 → 2026-05-18

50+ commits, Phasen 22-70, test-count 770 → 922, **0 trade-relevante Bugs offen**.
