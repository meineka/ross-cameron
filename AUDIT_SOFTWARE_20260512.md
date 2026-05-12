# Software- und Test-Audit — 2026-05-12

Zustand nach einem Trading-Tag mit Live-Bug (HSPT/ATRA), Bug-Fix-Iteration und
finalem Refactor-Pass.

## Inventur

| Layer | Vor Audit | Nach Audit |
|---|---|---|
| bot-Module | 33 Dateien, 3 800 LOC | 25 Dateien, 3 200 LOC |
| One-Off-Debug-Scripts | 11 (`_*.py`) | 0 (3 nach `tools/` verschoben, 8 gelöscht) |
| Test-Dateien | 14 | 15 |
| Tests gesamt | 125 | **141** |
| Presence-Tests | 21 | 21 (behalten als Sicherheits-Guards) |
| Behavior-Tests neu | – | **+16** |

## Befunde + Status

### 🔴 Kritische echte Bugs gefunden + gefixt

1. **`audit.classify_errors` ignorierte WARNING-Lines** ✅ gefixt
   - Folge: `spiral_lock` und `goal_reached` Patterns waren **unreachable**
   - Trotz korrekter Pattern-Definition wurden Spiral-Stops und Daily-Goal-Hits
     im Audit nie als info kategorisiert → fielen unter den Tisch
   - Test reproduziert + dokumentiert das jetzt

2. **`safe_bracket` ohne Liquidity-Check** ✅ gefixt heute Mittag
   - HSPT-Incident: `latest_trade.price` = stale, real-quote bid $7.50 / ask $0
   - `check_liquidity()` lehnt jetzt vor Submit ab

### 🟡 Strukturelle Defizite, dokumentiert für später

3. **`bot.py` ist God-Modul (1581 LOC, 39 Funktionen)**
   - Split-Kandidaten: `pattern_detector.py`, `executor.py`, `risk_engine.py`
   - Risiko: zu großer Refactor mid-Trading-Cycle, defer auf Wochenende

4. **Duplikation `safe_bracket.repair` vs `bot.protect_position`**
   - Beide implementieren OCO-Re-Protection, leicht abweichend
   - Konsolidieren in einem Helper

5. **Magic-Numbers in `bot.py` ohne zentrale Config**
   - SLIPPAGE_CENTS, POLE_MIN_MOVE_PCT etc. überall verteilt
   - Sollten in `constraints.yaml` und von dort geladen werden

### 🟢 Was sauber ist (kein Fix nötig)

- Filter-Module (`vwap_filter`, `float_filter`, `catalyst_filter`, `pump_dump_filter`,
  `indicators`) — sauber separiert, isoliert testbar
- Test-Coverage für Bracket-Logik, Pattern-Detection, Backoff komplett
- CI-Pipeline grün
- Secrets-Management ohne Hardcodes
- Heartbeat + Pre-Flight + Position-Recovery sauber

## Test-Coverage-Lücken geschlossen

Neue Tests in `test_behavior_audit.py`:

| Bereich | Tests | Was geprüft |
|---|---|---|
| `secrets_loader` | 3 | env vs file, comments, quoted values |
| `audit.classify_errors` | 2 | bekannte Lines → korrekte Kategorie (echte Behavior) |
| `delisted_cache` | 1 | Persistenz über Process-Restart |
| `reconnect_backoff` | 2 | Reset-Edge-Cases, Recovery nach Circuit-Breaker |
| `slippage_log` | 1 | JSONL-Format + Roundtrip |
| `two_source_scan` | 2 | Threshold-Boundary, Division-by-Zero |
| `position_recovery` | 2 | Alpaca-Fehler, mode=non-flatten no-op |
| `vwap_filter` | 1 | Zero-Volume graceful |
| `pump_dump_filter` | 1 | Score-Boundary (9 999 vs 10 000) |
| `catalyst_filter` | 1 | Cache-Hit verhindert yfinance-Call |

## Verbleibende technische Schulden

Priorisiert für nächste Iteration:

1. **bot.py-Split** (1-2 h, gut testbar dank vorhandener Coverage)
2. **constraints.yaml als Single-Source** statt Konstanten in bot.py
3. **Async-Tests** für ws_loop und time_and_health_loop (komplex)
4. **End-to-End-Replay-Test** mit echten historischen Bars (`backtest_day.py`
   als Test ausgeführt mit known ODYS-2026-05-11-Daten als Fixture)
5. **Konsolidieren** `safe_bracket.safe_bracket_buy` und `bot.submit_bracket_buy`
   in eine Methode

## Cleanup-Aktionen ausgeführt

| Was | Vorher | Nachher |
|---|---|---|
| `_buy_aapl.py`, `_buy_hspt.py`, `_buy_multi.py`, `_trade_trio.py` | one-off | **gelöscht** |
| `_final_state.py`, `_pdt_check.py`, `_wl_state.py`, `_ndx100_check.py` | one-off | **gelöscht** |
| `_morning_check.py`, `_movers_now.py`, `_pos_check.py` | im root | → `tools/` umbenannt |
| `audit.classify_errors` WARNING-handling | broken | **gefixt** |
| `test_behavior_audit.py` | – | **+16 echte Behavior-Tests** |

## Lessons Learned

1. **Presence-Tests sind besser als nichts, aber nicht genug**.
   `assert "foo" in src` schützt vor versehentlichem Löschen, sagt aber nichts
   über Korrektheit aus. Mindestens 1 Behavior-Test pro Funktion sollte
   existieren.

2. **One-Off-Scripts wachsen wie Unkraut**. Nach jeder Live-Session
   aufräumen. Was wirklich gebraucht wird → `tools/`. Rest weg.

3. **Schadhafte Code-Pfade können jahrelang unauffällig sein**, wenn
   sie nicht durch Verhaltenstests gedeckt sind. SPIRAL-DETECTION-Pattern
   war definiert aber **nie erreichbar** — niemandem aufgefallen weil keiner
   einen Test geschrieben hatte der die Klassifizierung mit echten Lines
   geprüft hätte.

4. **Source-greppen != testen**. Wenn ein Audit-Test nur prüft "import X
   in source", merkt er nicht ob X tatsächlich aufgerufen wird oder die
   Funktion das Richtige tut.
