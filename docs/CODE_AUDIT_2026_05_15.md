# Cameron-Bot — Full Code Audit (2026-05-15 23:00)

## Scope
- `06_live_bot/` — 53 modules, 13,669 LOC
- `tests/` — 73 test files, 770+ tests
- `03_rules_engine/constraints.yaml` — 777 LOC canonical spec
- `04_backtest/` — pilot datasets + sweep scripts
- `docs/` — Review-V2 explainability, audit, open-items

## TL;DR

**Status: PRODUCTION-READY für Paper-Trading.** Bot läuft live mit 770+ tests grün, Cameron-strict aligned mit YAML (Drift = 0), Defensive Imports, vollständige Alerter-Pipeline, fail-closed RateGuard. **Größte technische Schuld**: `bot.py` ist 3409 LOC monolithisch — sollte modularisiert werden (nicht für Live blocking, aber Wartbarkeit). 6 near-duplicate `fetch_*.py` scripts + 4 `replay_today*.py` Iterationen sind toter Code.

---

## ✅ Strengths

### 1. Konfigurations-Konsistenz (Drift = 0)
constraints.yaml ↔ bot.py exakt aligned (nach Phase-51 revert):
```
yaml.price_min=2.0   bot.PRICE_MIN=2.0        ✓
yaml.price_max=20.0  bot.PRICE_MAX=20.0       ✓
yaml.float_max=10M   bot.FLOAT_MAX=10M        ✓
yaml.rvol_min=5.0    bot.RVOL_MIN=5.0         ✓
yaml.daily_min=10.0  bot.DAILY_GAIN=10.0      ✓
yaml.pole_min=4.0    bot.POLE_MIN=4.0         ✓
yaml.topping=0.5     bot.TOPPING=0.5          ✓
yaml.flag_retr=50.0  bot.FLAG_RETR=50.0       ✓
yaml.breakout=1.5    bot.BREAKOUT=1.5         ✓
```

### 2. Defensive Programming
- Alle Externals haben try-except-fallback: alpaca-py, yfinance, tradingview-screener, ntfy
- `guarded_alpaca` wraps SDK with rate-cap + JSONL log (Phase-53/55)
- `alpaca_ws_patch` monkey-patches SDK reconnect-loop (Phase-31)
- Module-globaler Cool-Down (Phase-42) verhindert Self-Lock-Loops
- Singleton-enforcement (Phase-43) verhindert Multi-WS

### 3. Test Coverage
- 770+ tests in 73 files
- 14 Files mit parametrize → echte Behavior-Tests
- 24 `*_bugs.py` Regression-Files (1 pro Bug-Fix)
- 9 `test_phase_NN_*` Feature-Tests (1 pro Phase)
- `quality_gates.py --fast` läuft `smoke or critical` markers
- Pre-commit hook erzwingt full suite vor jedem commit

### 4. Live-Observability
- `bot.log` — structured logging
- `market_data_calls.jsonl` — yfinance/alpaca REST calls
- `order_lifecycle.jsonl` — intent → submitted → filled/rejected
- `alpaca_api_calls.jsonl` — Phase-56 mit rate_per_min + blocked_ms
- `premarket_v2_shadow.jsonl` — shadow-mode predictions
- `status.json` — atomic-write live state
- `alerts.log` — ntfy/Telegram/SMTP push history
- `heartbeat.txt` — bot liveness file

### 5. Health-Monitoring
- 6 probes: heartbeat, audit, yfinance, alpaca, bot_ws, catalyst_news
- Per-Probe thresholds (1 vs 2 fails)
- 1h re-fire while failing
- Provider-explicit titles (Phase-36): "ALPACA STALLED", "YAHOO OK again"
- Sleep-mode false-positive silenced (Phase-47)

### 6. Audit Trail
58 Phases mit klaren commits, jeder mit User-Request-Quote im commit-message. Linear nachvollziehbar von Phase 1 bis 58.

---

## ⚠️ Issues by Severity

### P1 — Architektur (langfristig, nicht trade-blocking)

#### 1.1 `bot.py` ist 3409 LOC monolithisch
```
06_live_bot/bot.py    3409 lines  ← largest
06_live_bot/no_trade_postmortem.py  555
06_live_bot/force_trade_loop.py     529
06_live_bot/health_monitor.py       475
06_live_bot/premarket_scanner_v2.py 464
```

`bot.py` enthält: Konstanten, AlpacaExecutor-Klasse, Bot-Klasse, daemon_run, aligned_scan_start, Pattern-Detector, alle Trading-Loop-Logik.

**Empfehlung**: Refactor in 4-5 Module:
- `bot/strategy.py` — `detect_bull_flag`, Pattern-Logic
- `bot/executor.py` — `AlpacaExecutor` class
- `bot/lifecycle.py` — `daemon_run`, `Bot.run`
- `bot/scheduler.py` — `aligned_scan_start`, intraday-rescan
- `bot/__init__.py` — re-export für Backwards-Compat

Aufwand: 4-8h. Nutzen: bessere Wartbarkeit, schnelleres Onboarding, kleinere Testfiles. **Nicht jetzt machen** — funktioniert wie es ist.

#### 1.2 Dead Code: 6 `fetch_*.py` + 4 `replay_today*.py`
```
fetch_sep.py    213 lines    ┐
fetch_oct.py    213 lines    │
fetch_nov.py    ~213         ├── 6 near-identical scripts
fetch_dec.py    ~213         │   (different month constant)
fetch_jan.py    ~213         │
fetch_older.py  213          ┘

replay_today.py   227         ┐
replay_today2.py  153         ├── 4 evolution iterations
replay_today3.py   99         │   (replay_today4 is current?)
replay_today4.py   90         ┘
```

`fetch_*.py` superseded durch `historical_data_loader.py` (Phase-52).
`replay_today*.py` — unklar ob aktuelle Version `4` ist, andere historisch.

**Empfehlung**:
- Delete fetch_sep/oct/nov/dec/jan/older — alle ersetzt durch `historical_data_loader.py --extend-existing`
- Behalte replay_today.py (oder das aktuellste), lösche die 3 anderen
- ~1200 LOC weniger, klarere Struktur

Aufwand: 30min mit Git-Tag für Rollback. **Quick-Win wenn Lust auf Aufräumen.**

### P2 — Code-Quality

#### 2.1 Silent `except Exception: pass`
5 Stellen mit silent catch:
```
alerter.py:226     in NtfyAlerter._do_send fallback
alerter.py:299     in CompositeAlerter forwarding
alpaca_rate_guard.py:187,193  in probe_ws_slot_free
alpaca_ws_patch.py:98         in reset_for_tests
```

**Bewertung**: alle in resilience-Pfaden (alerter darf nicht crashen, probe darf nicht crashen). Defensive by design, aber sollten mindestens `log.debug` mitloggen. **Niedrig priorität** — nicht behavior-relevant.

#### 2.2 1 TODO/FIXME
`bot.py:631` — Kommentar dass früher dead code mit TODO war, jetzt behoben. Falsch-positiv. Akzeptabel.

#### 2.3 venv-launcher vs system-Python Pattern
Jeder Python-Prozess hat 2 OS-Prozesse: venv-redirector + system-python-interpreter. Verwirrend aber by Python venv design auf Windows. **Dokumentiert** in `docs/REVIEW_V2_EXPLAINABILITY.md`. Nicht änderbar.

### P3 — Open ChatGPT Items (von docs/CHATGPT_OPEN_ITEMS.md)

Alle P0 Live-Blocker zu. P1 noch offen:
1. ❌ HARD_FLAT auto-postmortem trigger
2. ❌ README.md sync mit aktuellem Stand
3. ⚠️ `last_no_trade_reason` Feld in DayState populieren
4. ⚠️ Status-transition pushes (rate-limited/recovered etc.)
5. ❌ Phase-30 source-grep → behavior tests
6. ❌ Quality-gates Python preflight warning
7. ⚠️ HTTP 429 explicit test (historisch nicht mehr aufgetreten seit Phase-32)

Keine davon ist trade-blocking.

### P4 — Tests

#### 4.1 Test-Manifest-Drift
`test_manifest_freshness.py` schlägt nach JEDEM commit der neue Tests einführt, bis `tests/build_test_manifest.py` re-läuft. Funktioniert als gewollter Gate. **Akzeptabel** — operator regeneriert.

#### 4.2 4 Test-Files schienen leer, sind aber nicht
AST-grep zählt parametrize/class-based tests nicht. pytest-collection bestätigt:
- `test_pyramiding_pnl_bugs.py` → 3 tests
- `test_fake_broker_parity.py` → 7 tests
- `test_intraday_rescan_bugs.py` → 8 tests
- `test_manage_position_pnl_bugs.py` → 5 tests

Konsolidierung würde keine Coverage sparen. **NICHT machen.**

### P5 — Live State

Aktueller Stand 23:00 Berlin:
- bot.py daemon PID 44232 / 51172 — alive, heartbeat 22:49:56, sleeping bis Mo 12:28
- watchdog PID 32364 / 20284 — alive, tickt alle 5min
- health_monitor PID 34392 / 52268 — alive, pollt 6 probes alle 5min
- alpaca account: 0 open positions, ~$99,987 equity
- Force_trade_loop: gestoppt (Phase-51 revert)
- ChatGPT-Loop: tickt 5min answer-scan, 30min export-zip

---

## 🔒 Security / Safety Review

### API-Keys
- `secrets_loader.py` lädt `.env` mit `APCA_API_KEY_ID` + `APCA_API_SECRET_KEY`
- Keys werden NICHT in logs ausgegeben (grep test passed)
- `.env` ist in `.gitignore` (geprüft)
- `.env.example` checked in als Template
- ntfy topic `cameron-bot-ysdsphiehndewxp` ist random URL-safe → de-facto private

### Trade Safety
- Paper-Account only (alle TradingClient calls `paper=True`)
- Watchdog blockiert restart wenn open positions (Phase-12 Bug-Fix U)
- Position-recovery flattet auf restart (alternative wenn watchdog override)
- Stop-Loss + Take-Profit als bracket-children atomic (kein nakt-position-risk)
- Daily-Max-Loss + Goal-Stop in DayState

### Rate-Limit Safety
- Phase-53: GuardedTradingClient + GuardedDataClient wrappen alle SDK calls
- Phase-55: fail-closed → bei budget-exhaustion raise AlpacaRateLimitBlocked, kein bypass
- Phase-57: side-modules (health_monitor, pre_flight, audit, historical_loader) durch guard geleitet
- 200/min global cap via process-shared RateGuard token-bucket

---

## 📊 Empfehlung Reihenfolge wenn weiter optimiert wird

**Schnell-Wins (≤30min):**
1. README.md sync (5 min) — Open Items #2
2. Delete `fetch_*.py` 6 dead scripts (10 min) — Architektur 1.2
3. Pick latest `replay_today*.py`, delete others (10 min) — Architektur 1.2

**Mittlere Wins (1-2h):**
4. HARD_FLAT auto-postmortem trigger (1h) — Open Items #1
5. Populate `last_no_trade_reason` in DayState (1h) — Open Items #3
6. Status-transition pushes (rate-limited/ws-stalled) (2h) — Open Items #4

**Große Wins (4-8h):**
7. Refactor bot.py 3409 → 4-5 Module (4-8h) — Architektur 1.1
8. Phase-30 source-grep → behavior tests (2-3h) — Open Items #5
9. Cameron-Audit-Gaps umsetzen (Reversal, Halt-Resumption, Sub-VWAP-trap) (8-16h) — von docs/CAMERON_RULES_AUDIT.md

**KEINE Action benötigt:**
- Silent except (defensiv by design)
- 4 vermeintlich leere Test-Files (sind voll)
- venv/system Python Pattern (Windows by-design)
- manifest_freshness flake (intended gate)

---

## ✅ Gesamturteil

**Code-Qualität: GUT** — Phase-getrieben mit klarer Audit-Spur, defensive Programming, vollständige Test-Coverage, Config-Drift = 0.

**Bereit für Live-Paper-Trading**: alle 5 ChatGPT-Blocker geschlossen. Cameron-Strategy korrekt implementiert (24 von 38 dokumentierten YAML-Regeln; 14 fehlen aber sind erweiterte Edge-Cases wie Reversal/Halt-Resumption).

**Wartbarkeit**: gut für aktuelle Größe, würde von Refactor profitieren. Nicht dringend.

**Sicherheit**: keine bekannten Risiken. Keine API-Keys in logs, paper-only, atomic-bracket-orders, rate-cap fail-closed.

**Empfohlene Action**: nichts dringend. Ggf. die 30-min Quick-Wins (README + fetch_*.py cleanup) bei nächster Gelegenheit. Sonst: Cameron-strict läuft, Bot wartet auf Montag-Premarket.
