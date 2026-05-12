# Transfer-Dokument für neuen Trading-Bot

Komplette Erkenntnisse aus 2 Tagen Cameron-Bot-Entwicklung — Tech, Patterns,
Fallen, Tests, und 13 echte Bugs die wir gefunden haben.

Gedacht als Input für einen neuen Bot mit **anderer Strategie** —
trenne strikt zwischen *universal lessons* (gilt für jeden Bot) und
*Cameron-specific* (nur für Small-Cap-Bull-Flag).

---

## 1. Tech-Stack (was funktioniert)

### Trading-Infrastructure
| Komponente | Wahl | Warum |
|---|---|---|
| Broker | **Alpaca Paper** | Free, REST + WS, Bracket-Orders, US-Stocks |
| Real-Time-Daten | Alpaca WebSocket IEX-Feed | Free Tier, ~2% Marktvolumen |
| Historical-Daten | yfinance (Pre-Market-Scan), Alpaca Bars (Backtest) | yfinance ist rate-limited! |
| Order-Types | BRACKET (Entry+Stop+TP) und OCO | broker-side protection auch bei Bot-Crash |
| Language | Python 3.11 | alpaca-py SDK, asyncio |

### Anti-Patterns (Was NICHT funktioniert)
- ❌ **Alpaca SIP-Daten** → kostenpflichtig, IEX reicht
- ❌ **yfinance.Ticker(sym).news** → unzuverlässig, rate-limited, manchmal leer ohne Grund
- ❌ **yfinance daily-bars für mehr als ~5000 Tickers** → 429 Rate-Limit
- ❌ **`latest_trade.price` blind vertrauen** → kann stale sein (HSPT-Bug)

---

## 2. Architektur-Patterns (universal)

### Daemon-Mode mit Trading-Window
```
Sleep bis Premarket-Time → Scan → WS-Stream → Trade → HARD_FLAT → Sleep bis nächste Session
```
Heartbeat-File alle 60s damit externe Watchdogs Liveness sehen.

### Pre-Flight-Checks
Vor jedem Trading-Tag prüfen:
1. Broker-Auth funktioniert
2. WebSocket-Init funktioniert (smoke connect+disconnect)
3. Daten-Source ok (mit Warning-only-Fallback)

→ Verhindert "Geistermodus" wo Bot stundenlang läuft mit broken connection.

### Position-Recovery beim Restart
Bei Bot-Crash mit offenen Positions:
- Default: **flatten** (sicher, kein Risk-Add)
- Alternative: state-file-recovery (komplex)

### Multiple Schutz-Layer
1. **Script-Side**: Quick-Exit, MACD-Exit (in-bar reactions)
2. **Broker-Side**: BRACKET (Stop + TP)
3. **Process-Watchdog**: alle 5 Min Liveness
4. **Audit-Monitor**: alle 5 Min Log-Klassifizierung

Wenn Layer 1+2 funktionieren reicht das normalerweise. Layer 3+4 sind defense-in-depth.

### Aligned Re-Scan
Pattern-Detection schaut 5-min-Bars an. Scans **aligned zu round 5-Min-Boundaries**
(:00, :05, :10, ...) damit Pattern-State zwischen Restarts reproduzierbar ist.

---

## 3. Test-Strategie (universal)

### Pyramide (was wirklich Wert bringt)
```
                  E2E (Replay)              ← 3 Tests, 5 Min
                Integration (mock)          ← 30 Tests, 30 Sek
       Unit (compute_position_size etc.)    ← 140 Tests, 5 Sek
```

### Test-Klassen die echte Bugs fanden
1. **Boundary / Edge-Case** — entry==stop, NaN bars, empty list, negative price
2. **State-Machine** — was passiert nach T1 wenn shares=1? Cancellation race?
3. **Behavior tests** — `audit.classify_errors("ERROR ...")` → expected_category
4. **Source-grep als Smoke** — `assert "import X" in src` schützt vor Regression
5. **Replay-Regression** — voller Bot-Tag durchläuft, PnL vs Baseline

### Test-Klassen die NICHT fanden
- **Type-Hint-Tests** — null Wert
- **Coverage-Reports** — sagt nicht ob die richtige Sache getestet ist
- **Mocked-Broker-Tests ohne Edge-Cases** — bestätigt happy-path, übersieht Liquidity

### Goldener Test-Kommentar
> "Wenn Code-Änderung diesen Wert ändert: bewusst prüfen ob die Strategie das wollte"

(z.B. Baseline-PnL $10.38 → $7.08 nach strikteren Filtern — bewusst, also Baseline updaten)

---

## 4. Die 13 Bugs die wir gefunden haben

Klassifiziert nach Risiko-Typ. **Alle aus dem Cameron-Bot, alle in einem Tag.**

### Klasse A: Money-Risk (Position-Sizing)
| Bug | Was | Fix |
|---|---|---|
| **A1** | `account_equity`-Parameter nicht benutzt → Cameron's 1%-Rule ignoriert → Kleiner Account = Bankrott in 4 Trades | min(max_shares, equity * 0.01 / risk_per_share) |
| **A2** | Kein Min-Stop-Distance → bei risk_per_share $0.001 = 50000 Shares Position | risk_per_share = max(raw, $0.05) |
| **A3** | Negative Eingaben → positive shares | guard entry <=0 or stop<=0 |

### Klasse B: Order-Lifecycle
| Bug | Was | Fix |
|---|---|---|
| **B1** | BRACKET-Stop relativ zu Limit, fill kommt tiefer → Stop > Fill = invalid für long → Position ungeschützt | Post-Fill repair: cancel+OCO mit stop relativ zu fill |
| **B2** | Stale `latest_trade.price` → Limit + Slippage falsch berechnet | Vor Order: liquidity check (vol, two-sided quote, spread<5%) |
| **B3** | 1-Share-Trade nach T1 stuck (sells 0, position in_position=True forever) | T1 nur wenn shares >= 2; T2 ohne half_filled-Pflicht |

### Klasse C: State-Machine
| Bug | Was | Fix |
|---|---|---|
| **C1** | MACD-Exit-Win resettet `consecutive_losses` nicht → SPIRAL_LOCK fälschlich | else-Branch: reset bei pnl>0 |
| **C2** | T2 nur erreichbar via half_filled=True → 1-Share-Trades konnten T2 nie hitten | T2 wenn shares>0 |

### Klasse D: Async / Error-Handling
| Bug | Was | Fix |
|---|---|---|
| **D1** | `ws_reconnects` zählt nur Exceptions, keine clean disconnects | beide Pfade incrementieren |
| **D2** | backoff.sleep_after_fail() IMMER aufgerufen → Circuit-Breaker bei 8 sauberen Reconnects | had_error-Flag, backoff nur in error-path |
| **D3** | `handle_bar` ohne outer try/except → eine Anomalie killed alle Symbol-Bars | wrap in try/except mit log.error |

### Klasse E: Robustness
| Bug | Was | Fix |
|---|---|---|
| **E1** | `audit.classify_errors` ignorierte WARNING-Lines → spiral_lock + goal_reached unreachable | "WARNING" zur Liste der pre-filter |
| **E2** | Watchdog blind restart ohne Trade-Lock-Check | pre-check positions, blockt wenn open |

### Klasse F: Security
| Bug | Was | Fix |
|---|---|---|
| **F1** | **CRITICAL**: hardcoded API-Keys in watchdog.py committed | secrets_loader + Test scant **alle** .py-Files |

---

## 5. Cameron-spezifische Lessons (kann übersprungen werden für andere Strategie)

### 5-Pillars (Cameron's Stock-Selection)
1. **Price** $2-$20
2. **Float** <10 Mio Shares
3. **RVOL** ≥5× vs. 20-Day-Avg
4. **Daily-Change** ≥10%
5. **News-Catalyst** (24h)

### Bull-Flag-Detection (5-Min-Bars)
- Pole: 3-7 grüne Candles, ≥5% Move, Topping-Tail <40%
- Flag: 1-3 rote Candles, max 50% Retrace
- Breakout: grüne Candle über Flag-High, Volume >1.5× SMA20
- Vetos: VWAP-hold, MACD bullish (12/26/9), FBO-5-Indicator

### Pump-Dump-Detection
- Pre-Market-Score (RVOL × Δ%) > 10 000 = extrem
- Position-Size auf 25% reduzieren
- Cameron's $17k-Loss auf ODYS war so ein Profil

### Time-Cuts (NY-Time)
- **9:30 RTH-Start** — Market-Open
- **9:35 NEW_ENTRIES_START** — kein Entry in 1. 5 Min (Open-Volatility)
- **11:30 NEW_ENTRIES_END** — Cameron's Power-Hour-Ende
- **12:00 HARD_FLAT** — alle Positions zu

### Risk-Management
- Max-Loss-pro-Trade: $50 (Paper-Modus konservativ)
- Daily-Max-Loss: 3× Trade-Loss = $150
- Daily-Goal-Stop: bei +$150 stop
- Spiral-Stop: 2 Verluste in Folge = trading stop
- Quarter-Size-Rule: bis 20¢ kumuliert nur 25% Size
- 1:2 R:R Minimum (T1 = entry + (entry-stop))
- T2 = max(pole-height-target, next-psych-level)

---

## 6. Fallen wo wir auf die Nase gefallen sind

### "Latest Trade Price ist aktuell" (HSPT)
Bei illiquiden Stocks ist `latest_trade.price` ein **stale Print von Stunden**.
Real-Quote ist bid+ask. Order an Limit "über last_trade" kann **dramatisch** unter ausführen.
→ **Lesson:** IMMER check_liquidity vor Submit (vol > 10k, two-sided quote, spread <5%).

### "Test-Coverage = Test-Qualität"
21 von 125 Tests waren reine **Source-Greps** (`assert "import X" in src`).
Wahrscheinlich findest du JEDEN Bug der drinsteht nicht.
→ **Lesson:** Behavior-Tests > Presence-Tests. Mindestens 1 echter Test pro Funktion.

### "Stop-Loss in BRACKET ist genug"
Wenn fill stark unter Limit liegt, hat Alpaca den Stop-Loss-Child **silent rejected**.
Position lebt nur mit TP — **ungeschützt nach unten**.
→ **Lesson:** Post-Fill-Validation. Wenn stop >= fill: cancel + neue OCO.

### "Test-Theater"
Wir hatten Tests die behaupteten Pattern-Match-Counter wären reseted, aber
Real-Logik fired die Counter nie. Test grünt aber Bot defekt.
→ **Lesson:** Tests müssen REALE Log-Lines / Bar-Data simulieren, nicht mocked Zwischenzustände.

### "Watchdog ist die Backup-Sicherheit"
Watchdog hatte hardcoded API-Keys. Wenn das Repo public würde, kompromitiert.
→ **Lesson:** Sicherheits-Tests müssen **alle** Files prüfen, nicht Whitelist.

### "Pre-Market-Spike = guter Trade"
ODYS hatte Score 144 000 pre-market. Bei Open: collapsed −100% in 30 Sek.
WOK gleiche Geschichte. Klassisches Pump-Dump-Profil.
→ **Lesson:** Score-Threshold > 10k = position-size auf 25%, nicht voll long.

### "Bot wird mein Cameron sein"
Cameron tradet 20 000 Shares aus dem Bauch. Wir tradeen 50 Shares aus Risk-Engine.
Cameron's Tag: −$17k verloren bei Edge-of-Catastrophe. Bot: $0 (kein Trade) oder ein Mini-Win.
→ **Lesson:** Bot ≠ Trader. Bot's Edge ist Disziplin + Konsistenz, nicht Konviktion + Größe.

---

## 7. Was beim neuen Bot anders sein sollte

Wenn die neue Strategie **nicht** Cameron-Bull-Flag ist (z.B. ORB-Breakout,
Mean-Reversion, Pairs, Trend-Following):

### Behalten (universal)
- BRACKET-Orders + Post-Fill-Validation
- Liquidity-Check vor Submit
- Daemon-Mode mit Heartbeat
- Pre-Flight-Checks
- Position-Recovery beim Restart
- 1%-Equity-Cap im Position-Sizer
- Outer try/except in WS-Callbacks
- secrets_loader, niemals hardcoded keys
- Test-Pyramide mit Behavior-Tests

### Strategie-spezifisch ersetzen
- Pattern-Detection (`detect_bull_flag` → dein eigener Detector)
- Pillars (Stock-Selection-Filter)
- Time-Cuts (passend zur Strategie, z.B. ORB nur in ersten 30 Min)
- T1/T2/Stop-Levels (passend zur Volatilität deiner Assets)
- Watchlist-Quelle (Cameron: 5-Pillars-Scan; ORB: NDX-100; etc.)

### Strategien wo Cameron-Architecture nicht passt
- **Crypto** — 24/7-Markets, andere Liquidity-Profile
- **Forex** — Currency-Pairs, ECN-Broker
- **NDX-100 Mega-Caps** — andere Bewegungen, 200-EMA-Setups statt Bull-Flag
- **Options** — Greeks, Multi-Leg, ganz andere Risk-Engine

---

## 8. Empfehlung Workflow für neuen Bot

1. **Strategie-Doc** mit Regeln, Vetos, Risk-Limits (1-2 Seiten)
2. **Pattern-Detector als Pure-Function** (input: bars, output: signal/params/veto-reason)
3. **Backtest-Driven**: zuerst Replay auf historische Daten, dann live
4. **Tests von Tag 1**: Pattern-Detector mit edge-cases (empty, NaN, oversize)
5. **Filter-Module** isoliert (vwap, macd, rsi, volume — testbar einzeln)
6. **Bracket-Default**: jede Position broker-protected, Post-Fill-Validation
7. **Heartbeat + Watchdog** identisch übernehmen
8. **Audit-Monitor** mit projektspezifischen Patterns
9. **Backtest-Tool** als Daily-Job (validiert Filter-Wirkung)
10. **Eigene Constants.yaml** als Single-Source-of-Truth

---

## 9. Repo-Struktur die sich bewährt hat

```
ross-cameron/
├── 03_rules_engine/constraints.yaml    # SSoT (geplant, noch nicht fertig)
├── 04_backtest/                        # Pilot-Daten + replay
├── 05_data/                            # raw + cache
├── 06_live_bot/
│   ├── bot.py                          # Bot-Class + main (zu groß, 1620 LOC)
│   ├── audit.py                        # Log-Klassifizierung
│   ├── deploy_safe.py                  # Restart mit Trade-Lock
│   ├── watchdog.py                     # Process-Supervisor
│   ├── secrets_loader.py               # .env + env-vars
│   ├── pre_flight.py                   # Startup-Checks
│   ├── reconnect_backoff.py            # WS Exp-Backoff + CB
│   ├── slippage_log.py                 # Order-Fill-Drift-Tracking
│   ├── status_dashboard.py             # Live-state.json
│   ├── position_recovery.py            # Crash-Recovery
│   ├── day_summary_persist.py          # End-of-Day-JSON
│   ├── safe_bracket.py                 # Liquidity + Post-Fill repair
│   ├── delisted_cache.py               # yfinance-Tote skipenn
│   ├── vwap_filter.py                  # einzelner Filter
│   ├── macd / float / fbo / catalyst   # weitere Filter
│   ├── pump_dump_filter.py             # Risk-Multiplier
│   ├── two_source_scan.py              # Alpaca-Fallback
│   ├── backtest_day.py                 # CLI-Tool
│   └── tools/                          # Live-Debug-Helpers
│       ├── morning_check.py
│       ├── movers_now.py
│       └── pos_check.py
└── tests/                              # 173 Tests
```

Lessons fürs Layout:
- Filter-Module **klein und einzeln** (vwap=28 LOC, float=33 LOC) — leicht testbar
- bot.py wuchs zu **1620 LOC** — sollte gesplittet werden in pattern_detector.py /
  executor.py / risk_engine.py / bot.py
- `tools/` für one-off-Scripts (sonst wachsen wie Unkraut)

---

## 10. Konkrete Code-Snippets die du übernehmen kannst

Siehe `06_live_bot/safe_bracket.py` als Template für jeden Broker-Order-Submit.
Siehe `06_live_bot/reconnect_backoff.py` für jede Async-Reconnect-Loop.
Siehe `tests/test_pattern_robustness.py` als Template für deinen Pattern-Detector-Test.

---

## 11. Bottom-Line in einem Satz

> **"Defensive Code + Behavior-Tests + Broker-Side-Protection schlagen jede Strategie. Filter-Komplexität ist Bonus, Fehler-Robustheit ist Pflicht."**
