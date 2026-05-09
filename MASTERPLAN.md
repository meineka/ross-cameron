# Masterplan — Ross-Cameron-Strategie operativ

Stand: 2026-05-09 (final, konsistenz-validiert)

Dieser Plan ist die **operationale Übersetzung** der `constraints.yaml`. Er beantwortet
die Frage: **"Wenn ich Cameron's Strategie morgen tradese, was tue ich Schritt-für-Schritt?"**

Alle numerischen Werte stammen aus `03_rules_engine/constraints.yaml`. Bei Konflikt
zwischen diesem Dokument und der YAML gilt die **YAML als Ground-Truth**.

---

## TEIL 1 — Daily Trading SOP (Standard Operating Procedure)

### Phase 0 — Vor der Session (am Vorabend / 30 Min vor Open)

**00:01 Mental-Check**
- [ ] 30 Min Sport heute schon erledigt?
- [ ] 15 Min Meditation/Routine heute schon erledigt?
- [ ] Bin ich emotional aktiviert (Frust/FOMO/Revenge)? → wenn ja: heute **NICHT** traden
- [ ] Letzte 2 Tage rot? → daily-max heute auf 50 % reduzieren
- [ ] Letzte 4 Tage rot? → daily-max heute auf 25 % reduzieren

**00:02 Tools bereit**
- [ ] Lightspeed (oder TOS mit TIF=Extended) startklar
- [ ] 4 Charts pro Watchlist-Stock: 10s, 1m, 5m, daily
- [ ] Indikatoren auf jedem TF: 9 EMA (gray), 20 EMA (blue), 200 EMA (purple), VWAP (orange), MACD 12/26/9, Volume Bars
- [ ] Bollinger 20/2.0 nur auf 5m (für Reversals)
- [ ] Hotkeys gemappt: Shift+1..9 = Buy 1k–9k @ Ask+15¢, Ctrl+Z = Panic-Exit, Ctrl+L/K = Sell into ask
- [ ] News-Feed läuft (Bloomberg / NewsDesk / Twitter)
- [ ] Trade-Log-Sheet offen (Felder: setup, time, symbol, price, entry, exit, shares, pnl_usd, pnl_pct, notes)

**00:03 Daily-Plan setzen**
- [ ] Daily-Goal definiert (Default: $1.000; Hot-Day: $20k)
- [ ] Daily-Max-Loss = Daily-Goal (symmetrisch)
- [ ] Max-Loss/Trade definiert (Default: $500, oder ~1 % Konto)

---

### Phase 1 — Premarket-Scanning (07:00–09:30 ET)

**07:00 Watchlist bauen**

Pro Stock auf "Top Gappers"-Scanner: prüfe alle **5 Pillars** (alle müssen erfüllt sein):

| # | Pillar | Strict | Loose |
|---|---|---|---|
| 1 | Preis | $2 – $20 | (Sweet Spot **$5 – $10**) |
| 2 | Float | < 10 M Shares | < 20 M (mit besonderem Catalyst) |
| 3 | RVOL | ≥ 5× (vs 30-Tage-Avg) | — |
| 4 | Daily % Change | ≥ +10 % | (Gap-and-Go: ≥ +4 %) |
| 5 | News-Catalyst | Tier-1-Quelle | — |

**Tier-1-News-Quellen** (alles andere = suspect):
Company-PR, Earnings-Report, SEC-8K-Filing, FDA-Announcement, Government-Contract,
Top-Tier-Analyst-Firm, Exchange-Statement.

**Bonus-Boost** (Watchlist-Priority erhöhen wenn gegeben):
- Recent IPO (≤ 90 Tage)
- Recent Reverse Split (Float-Drop)
- All-Time-Highs / Blue Sky
- Yesterday Volume < 100k
- Float Rotation ≥ 1× heute (Volume / Float ≥ 1)
- Former Runner (historisch 100 %+ Move)
- Premarket-Volume ≥ 300k (sicher ≥ 1M)

**Veto** (Stock fliegt von Watchlist):
- Buyout-Stock (Preis fixed)
- Inside Day (komplett innerhalb Vortagesrange)
- News nicht von Tier-1-Quelle
- Notice of Delisting

**Output von Phase 1**: 1–3 Tickers (selten mehr) auf der Watchlist, sortiert nach Score.

---

### Phase 2 — Power Hour (09:30–10:30 ET)

**Universal-Trigger** für jedes Long-Setup:
> "First green candle to make a new high after a 1–3 candle pullback that
> didn't retrace > 50 % and didn't break VWAP — entry on that candle's break."

**Pre-Entry-Checkliste (jedes Mal):**
- [ ] Stock erfüllt 5 Pillars
- [ ] MACD positiv auf 1m (Signal-Line wird gestützt) — **HARD-SKIP wenn negativ**
- [ ] **False-Breakout-Filter** (5-Indikatoren-Check, ≥ 2 Treffer = SKIP):
  1. MACD against trade?
  2. Volume profile rot-heavy?
  3. History of false breakouts heute?
  4. Multiple topping tails?
  5. Too long consolidating (> 5–10 Kerzen sideways)?
- [ ] Long-Trade unter 200 EMA? → SKIP
- [ ] 4. Pullback in Folge? → SKIP (3. nur mit conditional_allow-Check)
- [ ] SPY/QQQ stark rot UND Setup nur durchschnittlich? → SKIP

**Entry-Modell-Auswahl** (welches Setup passt jetzt?):

| Kontext | Setup |
|---|---|
| Pole + enge Konsolidierung | **Bull Flag / Micro Pullback** (1m oder 5m) |
| PM-Gap > 4 % + Catalyst, Open-Drive | **Gap & Go** |
| Uptrend, Stock setzt auf VWAP zurück | **VWAP Bounce** |
| Stock öffnet rot, reklamiert dann Open | **Red-to-Green** |
| Stock im Trading-Halt → Resume | **Halt Resumption (Long)** |
| 4-Punkt-Struktur AB-BC-CD erkennbar | **ABCD Pattern** |
| Stock im Extrem (RSI<10/>90, Pin Bar, BB-Breach) | **Reversal** (Bottom-Bounce / Top-Reversal) |
| Stock pumpt, fällt unter VWAP, ≥2 failed reclaims | **Sub-VWAP-Trap** |
| Stock parabolisch (>100% Move, Halts) | apply Standard-Patterns mit reduzierter Size |
| News kommt **JETZT** raus, Stock reagiert | **Breaking News** |
| Stock macht 5–10 rote Kerzen + Pin Bar/Doji | **Candlestick-Reversal** |

**Position-Sizing (Quarter-Size-Rule, Cameron-Discovery Juni 2024):**
- Tagesstart: nur **¼ Max-Position** in den ersten Trades
- Erst wenn **+20 ¢/Aktie kumuliert** auf dem Tag → Full-Size freischalten
- Max-Loss/Trade: $500 → bestimmt Shares = $500 / (Entry − Stop)

**Scaling-In nach Entry (3k-Block-System):**
- Starter (anticipation): ¼ bis ⅕ der Full-Size
- Add bei first-candle-new-high: nochmal ein Block
- Add bei HOD-Break: nochmal ein Block
- Add jede 10 ¢ + jeder Micro-Pullback: weiter +Block (¼-Block am Top)
- **4 Continue-Signale müssen ALLE gegeben sein** zum Adden:
  1. Green orders dominant in T&S
  2. Level-2 Ask shrinks (kein Hidden Seller)
  3. Price moves up
  4. Stock at new HOD
- Wenn ein Signal fehlt → **stop adding**, nur halten oder skalen

**Hot vs Cold Market Anpassung:**
- Hot: Ziel-Max-Pos 20.000 Shares, mehr Adds
- Cold: Ziel-Max-Pos 6.000 Shares, Hit-and-Run Base-Hits

**Trade-Management (während offen):**
- Stop sofort auf BE bei +1R
- Sell-Half bei jedem definierten Target-Level (Ratio-basiert via Hotkey)
- "Add to Winners, NEVER to Losers" — hart
- "Best trades work almost immediately" — wenn nach 1–2 Min nichts passiert: oft Falle

**Exit-Trigger (gestaffelt):**
- Scale-Out 50 % bei 1R / first resistance → Stop auf BE
- Scale-Out 25 % bei 2R / measured move → trail unter 9 EMA
- Letzte 25 %: trail unter 9 EMA bis Bruch

**Hard-Exit-Signale (sofort raus):**
- MACD-Cross-Down auf 1m
- 1m-Close unter 9 EMA nach extended Move
- 1m-Close unter VWAP
- Big Seller 100k+ Shares auf Ask
- Hidden Seller (sustained sells, Ask shrinkt nicht)
- Burst red orders in T&S (Selling-Surge)
- Jackknife-Kerze (rapid up-down within one bar)
- Pop + dramatic Reversal (Topping Tail = ugly chart)
- Multiple rejections an 200 EMA
- Bruch unter 200 EMA auf Intraday-TF
- Zeit > 11:30 ET (cut runners)
- Zeit > 12:00 ET (hard flat)
- 50 % der Tagesgewinne zurückgegeben → Session-Stop

**Goldene Regel**: "Correct exit feels too soon" — wenn Exit "obvious", bist du schon spät.

---

### Phase 3 — Late Morning Decision (10:30–11:30 ET)

- Bisher grün? → ggf. ein letztes A-Quality-Setup mitnehmen, dann flat
- Cushion ≥ $400 aufgebaut? → Stop auf $100 daily-min ziehen, **niemals ins Minus**
- Bisher rot? → **Spiral-Mechanik prüfen**:
  - Stage 1 (Big unexpected loss): STOP, Pause
  - Stage 2 (Revenge Trading): erkennen + abbrechen
  - Stage 3 (B/C-Quality-Setups): "Triggerwords" beobachten ("I can't believe it…")
- 30 Min ohne Setup → Session beenden

---

### Phase 4 — Hard Flat (11:30 ET → 12:00 ET)

- 11:30: keine **neuen** Entries mehr
- Bestehende Runners: trail tight (unter 9 EMA, immer enger)
- 12:00: **alles flat**, Tag beendet
- After-Hours (16:00–20:00): **NICHT traden** — für Cameron nicht profitabel

---

### Phase 5 — Post-Session-Review (täglich)

- [ ] Alle Trades im Log mit allen Pflicht-Feldern
- [ ] Win-Rate heute? (Ziel: ≥ 65 %)
- [ ] R/R-Ratio heute? (Ziel: ≥ 2:1)
- [ ] An welcher A-Quality-Density war heute der Markt? (hot 15–25 / medium 10–15 / cold 0–10)
- [ ] Habe ich Quality-Threshold reduziert? → Spiral-Warnsignal
- [ ] Welche Setups funktionierten? Welche nicht?
- [ ] Pattern-Sammlung: Screenshots der besten + schlechtesten Trades

**Monatlich:**
- Aggregierte Stats: Win-Rate, P/L-Ratio, Avg-Win/Loss, Anzahl-Trades
- Cameron-Benchmark-Vergleich:
  - Win-Rate Ziel: 65–70 %
  - Avg Winner ~$1.300–$1.800 (skaliert mit Share-Size)
  - Hold-Time-Ziel: ~3 min Winner, ~2 min Loser
- Welche Setups haben Edge? Welche nicht?

---

## TEIL 2 — Risk-Framework (zusammengefasst)

```
HARD CAPS:
  reward_to_risk_ratio        ≥ 2.0
  max_loss_per_trade_usd      $500 (Default; ~1 % Konto)
  daily_max_loss              = daily_goal (symmetrisch)
  intraday_drawdown_rule      50 % Tagesgewinn-Rückgabe → STOP
  red_streak_after_2_days     daily-max auf 50 %
  red_streak_after_4_days     daily-max auf 25 %
  add_to_losers               VERBOTEN
  long_below_200ema           VERBOTEN
  short_above_200ema          VERBOTEN

ACCURACY-TARGETS:
  min                         50 %  (mit 2:1 R/R immer noch profitabel)
  optimal                     65 %
  stretch                     70 %
  Cameron live (winning days) 71 %
  Cameron live (losing days)  50 %  ← Spiral-Warnung

POSITION-SIZING:
  formula                     shares = $500 / (entry - stop)
  quarter_size_start          ¼ Max bis +20¢/Share kumuliert
  scaling_block_size          3.000 Shares
  starter_fraction            ⅕ – ¼
  max_pos_hot_market          20.000 Shares
  max_pos_cold_market          6.000 Shares
  liquidity_cap               ≤ 1 % avg 1m-Volume
  pdt_minimum_account         $25.000 (US PDT-Regel)
  leverage_us_margin          4×
  leverage_international      6×
```

---

## TEIL 3 — Vetos (Trade-Skip-Liste)

**Nimm den Trade NICHT** wenn ein Veto greift:

1. Kein klarer News-Catalyst
2. Float > 20M ohne außergewöhnlichen Catalyst
3. Preis < $2 (strict) oder > $20
4. RVOL < 5×
5. Tagesbewegung < +10 % zum Watchlist-Eintrag
6. Pole hat Topping-Tail (oberer Docht > 40 % Range)
7. Konsolidierung bricht VWAP nach unten
8. ≥ 4 rote Pullback-Kerzen in Folge
9. Pullback-3 ohne alle conditional_allow-Bedingungen erfüllt
10. Pullback-4+ (immer skip)
11. Big Seller 100k+ auf Ask sichtbar
12. Kauf in laufendem Volumen-Spike (FOMO)
13. Kauf AM Top einer long-body green candle (Chasing)
14. Long-Trade unter 200 EMA
15. Short-Trade über 200 EMA
16. Inside Day
17. Buyout-Stock
18. News-Quelle nicht Tier-1
19. Trade während Peak-News-Volatilität (warten auf "dust to settle")
20. Adding to Loser
21. Single-Trade-Risk > 1.5× avg Risk pro Tag
22. SPY/QQQ stark rot UND Setup nur durchschnittlich
23. Zeit > 11:30 ET für neue Entries
24. Konto bereits bei daily_max_loss
25. 50 % der Tages-Gewinne zurückgegeben
26. 30 Min ohne Trade-Setup → Session-Stop
27. Aktie im Trading-Halt nach unten
28. Emotional aktiviert (Frust/FOMO/Revenge)

---

## TEIL 4 — Order-Routing (kritisch für Pre-Market)

```
HARD-RULES:
  no_raw_market_orders            (in Extended-Hours sowieso geblockt)
  use_marketable_limit_with_offset
  standard_offset_cents           +15¢ Buy / −15¢ Sell

EXTENDED-HOURS (07:00–09:30 + 16:00–20:00):
  forbidden                        market orders, stop orders, options, leverage-after-16:00
  allowed                          limit orders with offset, leverage pre-market
  gtc_orders                       sit passive, trigger only at 09:30 open

BROKER-SETUP:
  Lightspeed                       limit + TIF=Day works automatically 04:00–20:00
  Thinkorswim                      TIF must be set to "Extended"
  Webull                           Turbo Trader Settings: offset orders + percentage exits

HOTKEY-MAPPING:
  Shift+1 to 9                    Buy N×1000 @ Ask+15¢
  Ctrl+Z                          Panic-Exit full @ Bid−15¢
  Ctrl+L / Ctrl+K                 Sell-Half/Full into Strength @ Ask
  Ctrl+(any)                      Cancel orders
```

**Goldene Routing-Regel**: "Buy at ask, sell into ask (when bullish) — NEVER hit bid as planned exit."

---

## TEIL 5 — Halt Mechanics (kanonisch)

```
LULD CIRCUIT BREAKERS:
  Level-1 trigger    10 % move in 5 min
  Level-2 trigger    20 % above 5-min average
  bid_hold_required  15 seconds above LULD level
  resume_pattern_up  often resume higher (clean break) or limp
  resume_down        often resume lower

T-12 NO-NEWS HALTS:
  risk_higher_on     NYSE
  risk_lower_on      NASDAQ
  boilerplate_resp   "no material developments"

ANTI-PATTERN:
  pinning            stock oscillates around LULD without break → SKIP
```

---

## TEIL 6 — Beginner-Roadmap (für eigene Skalierung)

| Phase | Dauer | Share-Size | Daily-Goal | Ziel |
|---|---|---|---|---|
| 1 — Simulator | 90 Tage | (sim) | konsistent grüne Wochen | Pattern-Recognition, Disziplin |
| 2 — Micro-Real | 1.000 Trades | 10 Shares | $0,10/Tag | Track-Record, Konsistenz |
| 3 — Skalierung | 2.000 Trades | 160 Shares | nach Konsistenz | $10k cumulative |
| 4 — Pro | langfristig | bis 20.000+ | nach Hot-Day-State | Cameron-Niveau |

**Skalierungs-Regeln throughout:**
- Skalieren NUR nach bewiesener Konsistenz, nicht nach Bauchgefühl
- Daily-Goal verdoppeln nur wenn aktuelle Share-Size 30 Tage stabil
- **Erst Accuracy fixen, dann P/L-Ratio, dann Größe**

---

## TEIL 7 — Was diskretionär bleibt (algorithmisch schwer)

- Tape-Reading (Level 2 + Time & Sales) — durch Heuristiken approximierbar
- "Stärkster Stock des Tages"-Auswahl — durch Scanner-Score (Float/RVOL/Gap/Catalyst-Composite)
- Hidden-Buyer/Whale-Detection — siehe `level2_reading.md` für Schwellen
- Marktphase Hot/Medium/Cold — manuelle Kalibrierung der Aggressivität
- Catalyst-Qualitäts-Bewertung — Kontext-Verständnis nötig (z.B. $30M-Contract vs $300M-Negotiation)

---

## TEIL 8 — Daily Cheat-Sheet (1-Seiten-Druck-Version)

```
PRE-MARKET (07:00):
  □ 5 Pillars: $2-$20, <10M Float, RVOL≥5x, %Δ≥10%, News-Tier1
  □ Bonus: IPO, Reverse-Split, Blue-Sky, Vortagesvol<100k
  □ Watchlist: Top 1-3 Tickers
  □ Quarter-Size-Modus aktiv

ENTRY-PRÜFUNG:
  □ MACD positiv auf 1m
  □ False-Breakout-Filter <2 Treffer
  □ Universal-Trigger: green candle bricht prev red high
  □ Burst grüner Orders im T&S
  □ Volume ≥ 1.5x SMA(20)

POSITION-MANAGEMENT:
  □ Risk pro Trade max $500
  □ Stop = pullback low (oder LOD bei Reversal)
  □ Add bei micro-pullback + 4-Continue-Signale
  □ Block-Size 3000 Shares
  □ +20¢/Share kumuliert → Full-Size unlock

EXITS:
  □ Scale-Out 50% bei 1R, BE-Stop
  □ Scale-Out 25% bei 2R, trail 9EMA
  □ Letzte 25%: trail bis 9EMA-Bruch
  □ MACD-Cross-Down = sofort raus
  □ 11:30 = keine neuen Entries
  □ 12:00 = hard flat

KILL-SWITCHES:
  □ daily_max_loss erreicht
  □ 50% Tagesgewinn zurückgegeben
  □ 30 Min ohne Setup
  □ emotional aktiviert
  □ ≥2 False-Breakout-Filter-Treffer
```

---

## Anhang — Wo ist was?

| Du suchst | Datei |
|---|---|
| Alle harten Regeln (Single-Source) | `03_rules_engine/constraints.yaml` |
| Konsolidierungs-Audit | `03_rules_engine/CONSISTENCY.md` |
| Setup-Details (1 pro Datei) | `02_setups/*.md` |
| Quellen-Quotes mit Belegen | `01_strategy_breakdown/*.md` |
| YouTube-Transkripte | `notes/transcripts/*.txt` |
| Buch-Volltext (Kap. 1–5) | `notes/cameron_book_chapter5.txt` |
| Offene Fragen | `notes/open_questions.md` |
| Memory / Brain-State | `_brain/MEMORY.md` etc. |

---

## Was als nächstes konkret zu tun ist

1. **Markt-Entscheidung** (US Small Caps original vs MT5-Übertragung) — siehe `notes/open_questions.md`
2. **Daten-Pipeline** für 1m-Bars + Premarket + Float + News
3. **Backtest-Skelett** das `constraints.yaml` lädt
4. Erstes Setup zu backtesten: **Bull Flag / Micro Pullback** (sauberster Start)
5. Stats vergleichen mit Cameron-Benchmarks (71 % Acc, 14 ¢/Share Avg-Winner)
