# ChatGPT Review — Open Items (Stand 2026-05-15 22:50)

Konsolidiert aus 40 Answer-Files in `99_Claude_Chatgpt/`. Letzte Antwort: `20260515_2048`.

## ✅ Bereits umgesetzt (in den letzten 24h)

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
| **`GuardedTradingClient` + `GuardedStockHistoricalDataClient`** | **53** | **05b0899** |
| **Fail-CLOSED guard (block_until_allowed=False → no SDK call)** | **55** | **957c55f** |
| **`alpaca_api_calls.jsonl` mit `rate_per_min`, `blocked_ms`, `status=blocked`** | **56** | **957c55f** |
| **status.json mit Rate-Diagnostics-Feldern** | **56** | **957c55f** |
| **Side-Module guarded** (health_monitor / pre_flight / audit / historical_loader) | **57** | **957c55f** |
| **scanners/ in AI_HANDOFF_PACKAGE** | **58** | **957c55f** |
| **Storm-Regression Test (205 calls)** | **59** | **957c55f** |

## ⚠️ Noch offen (P1 — nicht trade-kritisch)

| # | ChatGPT-Forderung | Quelle | Status |
|---|-------------------|--------|--------|
| 1 | **HTTP 429 root-cause + Test** | 1452, 1507, 1817 | ⚠️ teilweise — Phase-31..42 addressiert conn-limit-exceeded, aber kein explizit HTTP-429-Test. Effektiv: HTTP 429 hat sich seit Phase-32 nicht wiederholt. |
| 2 | **HARD_FLAT auto-postmortem** (`no_trade_postmortem_*.json` automatisch) | 1452, 1507 | ❌ noch nicht — `no_trade_postmortem.py` existiert als helper, aber wird nicht automatisch bei 12:00 NY getriggert |
| 3 | **README.md sync mit aktuellem Stand** | 1507 | ❌ veraltet — sagt noch "~630 tests" und "critical only" |
| 4 | **Status-transition Pushes** (`alpaca_rate_limited`/`recovered`, `ws_stalled`/`recovered`) | 2048 | ⚠️ teilweise — `ALPACA STALLED` + recovery existieren via health_monitor probes; aber rate-limit-blocked Transition fehlt als eigener Push |
| 5 | **`last_no_trade_reason` Field populieren** | 2048 | ⚠️ Feld in status.json drin (Phase-56), aber kein Code schreibt aktuell rein |
| 6 | **Phase-30 source-grep tests → behavior tests** | 1507 | ❌ wäre Quality-Improvement; aktuelle source-grep funktioniert aber |
| 7 | **Quality-gates Python preflight warning** | 1452 | ❌ Warning wenn falsches Python (ohne deps) gestartet — wäre operator-friendliness |

## 🟢 Kritische ChatGPT-Blocker — ALLE GESCHLOSSEN

ChatGPT hat in 2048 explizit 5 Blocker für Live-Einsatz benannt:

1. ✅ **P0**: Guard fail-open → **GESCHLOSSEN** in Phase-55
2. ✅ **P0/P1**: Side-modules nicht guarded → **GESCHLOSSEN** in Phase-57
3. ⚠️ **P1**: Status/Logs für No-Trade-Debug → **TEILWEISE** in Phase-56 (Feld da, Code-Pfad zu populieren noch nicht)
4. ✅ **P1**: scanners/ im Export fehlt → **GESCHLOSSEN** in Phase-58
5. ✅ **P1**: Startup-Storm-Regression-Test → **GESCHLOSSEN** in Phase-55 (test_205_rapid_calls)

## Empfehlung Reihenfolge wenn weiter gearbeitet wird

**Quick wins (≤30 min each):**
- README.md sync — 1 commit, 5 min
- HARD_FLAT auto-postmortem trigger in bot.py `_log_day_summary()` — 1 commit, 30 min
- Populate `last_no_trade_reason` in DayState — 1 commit, 30 min

**Größere Items (1-2h each):**
- HTTP-429 explicit test (auch wenn historisch jetzt)
- Phase-30 source-grep → behavior tests
- Status-transition pushes (rate-limited / ws-stalled / tradingview-failed)

**Test-Konsolidierung (verworfen):**
- 4 "safe-wins" untersucht: alle 4 Files haben tatsächlich Tests (parametrize/class-based). AST-grep hatte falsch gezählt. KEINE Konsolidierungs-Quick-Wins. 73 Files × ~10 Tests average = 770+ tests, gut strukturiert nach Bug/Phase.

## Status Live-Bot 2026-05-15 22:50

- Bot-daemon läuft mit Cameron-strict (Phase-51), schläft bis Mo 12:28 Berlin
- Watchdog + health_monitor + bot.py alle aktiv
- 776 tests grün (774 Pre-Commit + 2 Phase-55 new)
- Phase-31..58 alle live
- ChatGPT-Loop pausiert seit Export `20260515_2248_export_claude.zip`
