# Ross Cameron Modelling Space

Brain-GIMP zum Analysieren und Nachbauen von Ross Camerons Trading-Strategien
(Warrior Trading). Aus öffentlichen Setups eine formal beschreibbare,
backtestbare und algorithmisch ausgeführte Strategie.

## Status (Mai 2026 — Phase-58 deployed, ChatGPT P0 alle geschlossen)

- **Strategie-Recherche abgeschlossen** für Tier-1-Material (10+ YouTube-Videos,
  3h-Masterclass, Buch Kap. 1–5, Warrior-Trading-Artikel)
- **`03_rules_engine/constraints.yaml`** (777 Zeilen) = Single Source of Truth,
  alle Konflikte aufgelöst, kanonische Definitionen für `halt_mechanics` und
  `pullback_count_rule`.
- **Live Paper-Bot** in `06_live_bot/`: Bull-Flag-Strategie auf Alpaca paper
  account, 58 deployment phases, 770+ tests grün.
- **Pilot-Backtest-Dataset** in `04_backtest/data_pilot/`: 895k+ 5-min bars,
  ~1450 Symbole, 167+ Trading-Tage.

## Architektur

| Ordner | Inhalt |
|---|---|
| `01_strategy_breakdown/` | Buch/Masterclass/Video-Notes, Setup-Übersicht |
| `02_setups/` | 10 .md Setup-Beschreibungen (Bull Flag, Reversal, Sub-VWAP, ...) |
| `03_rules_engine/` | **`constraints.yaml`** = SSoT, 777 Zeilen kanonische Cameron-Spec |
| `04_backtest/` | Pilot-Datasets (parquet), backtest scripts, sweeps |
| `06_live_bot/` | Live Paper-Bot (53 modules, 13,669 LOC) — siehe unten |
| `tests/` | 770+ tests, 73 files (parametrize + bug regressions + phase tests) |
| `docs/` | REVIEW_V2_AUDIT, REVIEW_V2_EXPLAINABILITY, TEST_MANIFEST, CAMERON_RULES_AUDIT, CHATGPT_OPEN_ITEMS, CODE_AUDIT |
| `99_Claude_Chatgpt/` | ChatGPT review-handoff zips + answer files |

## Live-Bot (`06_live_bot/`)

Kern-Module (Stand Phase-58):
- `bot.py` — Strategy + AlpacaExecutor + daemon-loop (3409 LOC)
- `watchdog.py` — supervises bot.py, auto-restarts with safety-gates (Phase-54)
- `health_monitor.py` — 6 probes (heartbeat, audit, yfinance, alpaca, bot_ws, catalyst_news) (Phase-25/34)
- `alerter.py` — ntfy/Telegram/SMTP/Log composite (Phase-25, Phase-48 JSON-API)
- `guarded_alpaca.py` — `GuardedTradingClient`/`GuardedStockHistoricalDataClient`
  wrappers; fail-closed 200/min rate cap + `alpaca_api_calls.jsonl` (Phase-53/55/56)
- `alpaca_ws_patch.py` — monkey-patches alpaca-py reconnect-loop with
  schedule 5/60/120/180/300s + 90s global cool-down + singleton (Phase-31/41/42/43)
- `alpaca_rate_guard.py` — token-bucket 200/min (Phase-35)
- `scanners/tradingview_scanner.py` — TradingView primary scanner + Alpaca fallback
- `structured_logger.py` — `market_data_calls.jsonl` + `order_lifecycle.jsonl` (Phase-22/26)
- `historical_data_loader.py` — extend pilot dataset via Alpaca historical (Phase-52)
- `force_trade_loop.py` — demo/testing tool, bypasses all filters (Phase-45..49)

Detailed cycle/safety docs in `docs/REVIEW_V2_EXPLAINABILITY.md`.

## Quality Gates

```bash
# Fast gate (smoke + critical, ~30 sec): blocking pre-commit
.venv/Scripts/python.exe tests/run_quality_gates.py --fast
# expected: 215+ passed, 548 deselected

# Full suite (~1.5 min)
.venv/Scripts/python.exe -m pytest tests/ -q
# expected: 777 passed, 1 skipped, 1 warning

# Test manifest freshness check
.venv/Scripts/python.exe tests/build_test_manifest.py --check --no-collect
```

Note: must use `.venv/Scripts/python.exe` (project venv), NOT system Python.
System Python lacks deps (`alpaca`, `yfinance`, `pyarrow`, etc.).

## Kern-Cameron-Werte (Quick-Reference, Stand 2026-05-15 Cameron-strict)

| Bereich | Wert |
|---|---|
| Universum | US Equities, Preis $2–$20 (Sweet Spot $5–$10) |
| Float | < 10M strict (< 20M loose, < 5M "rocket fuel") |
| RVOL | ≥ 5× (vs 30-Tage-Avg) |
| Tagesbewegung | ≥ +10 % |
| News-Catalyst | Pflicht (Tier-1-Quellen bevorzugt) |
| Time-Window | 07:00–11:00 ET (Power-Hour 09:30–10:30) |
| Indikatoren | 9/20/50/200 EMA, VWAP, MACD 12/26/9, Bollinger 20/2.0, RSI 14 |
| Bull-Flag Pole | 3-7 grüne Kerzen, ≥ 4% Move, Topping-Tail < 50% |
| Bull-Flag Flag | 1-3 rote Kerzen, ≤ 50% Retrace, hold VWAP |
| Bull-Flag Breakout | 1.5× Vol-Spike, candle close confirmation |
| R/R-Ziel | 2:1 minimum, T2 at 3.5R cap |
| Max-Loss/Trade | $50 paper / $500 live; Daily-Max = Daily-Goal symmetrisch |
| Position-Sizing | Quarter-Size-Rule (unlock 10:00 NY), 3k-Block-Scaling |
| Universal-Trigger | "First green candle to make new high after pullback" |
| Bar-Timeframe | 5-min primary, 1-min context |
| Max-Risk-pro-Trade | 5% (was Phase-33-demo 7%) |

## Phase-Historie (deployed)

| Phase | Was wurde gemacht |
|---|---|
| 1-21 | Bot-Grundlagen, Pattern-Detection, Position-Mgmt, Replay-Testing |
| 22-29 | Structured Loggers, Audit-Iteration, Review-V2 Explainability |
| 30 | Trade-event push notifications |
| 31 | alpaca-py WS reconnect-backoff patch |
| 32 | aligned_scan_start cascade-storm fix |
| 33 | "see-some-trades" demo (later reverted in Phase-51) |
| 34 | bot_ws health probe |
| 35 | Alpaca RateGuard 200/min + 5s stall-probe |
| 36 | 1m bars + provider-explicit STALL/OK alerts |
| 37/40/44 | Startup push `[INFO] Bot started` |
| 38/39/41/42 | WS reconnect-schedule + cool-down evolution |
| 43 | StockDataStream singleton enforcement |
| 45-50 | force_trade_loop demo tooling (Phase-49: 2-min trend-trade) |
| 51 | Revert to Cameron-strict |
| 52 | historical_data_loader |
| 53 | GuardedTradingClient + GuardedStockHistoricalDataClient |
| 54 | Watchdog-blocked auto-push |
| 55 | Fail-CLOSED guard (P0 ChatGPT-blocker) |
| 56 | rate_per_min in JSONL + status.json diagnostics |
| 57 | Side-modules guarded (health_monitor / pre_flight / audit / loader) |
| 58 | scanners/ in AI_HANDOFF_PACKAGE export |

## Wichtigste Quellen-Hierarchie bei Konflikten

1. **Videos 2024** (aktuellste Cameron-Praxis) — Default
2. Buch 2015 (Kontext + Disziplin-Grundlagen)
3. Warrior-Trading-Webartikel (Aufbereitung)

Konflikt-Tabellen: `01_strategy_breakdown/book_notes.md` und `video_notes.md`.

## Live-Status

Aktueller Live-Stand siehe:
- `06_live_bot/status.json` — atomic-write live JSON
- `06_live_bot/heartbeat.txt` — bot liveness file
- `06_live_bot/bot.log` — structured logging
- `06_live_bot/alpaca_api_calls.jsonl` — guarded Alpaca calls with rate_per_min
- `06_live_bot/alerts.log` — ntfy/Telegram/SMTP push history

## Offene Items

- 7 P1 von ChatGPT review (siehe `docs/CHATGPT_OPEN_ITEMS.md`) — keine trade-blocker
- 14 Cameron-Regel-Gaps (Reversal/Halt-Resumption/Sub-VWAP-trap etc.) in
  `docs/CAMERON_RULES_AUDIT.md`
- Code-Audit (Stand 2026-05-15) in `docs/CODE_AUDIT_2026_05_15.md`
