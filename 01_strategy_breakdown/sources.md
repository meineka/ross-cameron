# Quellen — Ross Cameron Constraints

Stand der Recherche: 2026-05-09. Alle Werte in `03_rules_engine/constraints.yaml`
sind aus diesen Quellen abgeleitet. Bei Konflikten zwischen Quellen wurde der
in Camerons eigenem Buch / Warrior-Trading-Hauptseite genannte Wert priorisiert.

## Primärquellen (offiziell von Warrior Trading / Ross Cameron)

- Momentum Day Trading Strategies — https://www.warriortrading.com/momentum-day-trading-strategy/
- Momentum Trading Strategies (Hub) — https://www.warriortrading.com/momentum-trading-strategies/
- Bull Flag Chart Pattern & Trading Strategies — https://www.warriortrading.com/bull-flag-trading/
- How to Trade the Bull Flag Pattern — https://www.warriortrading.com/how-to-trade-the-bull-flag-pattern-with-confidence/
- Stock Selection PDF — https://cdn.warriortrading.com/warriortrading.com/assets/Warrior%20Trading%20-%20Stock%20Selection.pdf
- Day Trading Scanners — https://www.warriortrading.com/day-trading-scanners/
- How to Use Stock Scanners — https://www.warriortrading.com/how-to-use-stock-scanners/
- Power Hour in Stocks — https://www.warriortrading.com/power-hour-stocks/
- Best Time to Day Trade — https://www.warriortrading.com/best-time-to-day-trade/
- 1-Minute Scalping Strategy — https://www.warriortrading.com/1-minute-scalping-strategy/
- ABCD Pattern — https://www.warriortrading.com/abcd-pattern/
- MACD Indicator Strategies — https://www.warriortrading.com/macd-indicator-trading-strategies/
- EMA Explained — https://www.warriortrading.com/exponential-moving-average/
- Top 4 Indicators — https://www.warriortrading.com/top-4-indicators-day-trading/
- Day Trading From Red to Green — https://www.warriortrading.com/day-trading-my-way-from-red-to-green/
- Red Day Lessons — https://www.warriortrading.com/red-day-lessons-with-ross-cameron/
- Behind the Trades Ep. 8 (Metrics) — https://www.warriortrading.com/behind-trades-metrics-profitable-trader-ep-8/
- Cameron persönliche Seite — https://www.rosscameron.com/
- YouTube Kanal — https://www.youtube.com/@DaytradeWarrior
- Buch (How to Day Trade, Cameron 2015) — offizieller PDF-Auszug (Kap. 1–5):
  https://media.warriortrading.com/2018/11/5CH_How_To_Day_Trade_Ross_Cameron_Warrior_Trading.pdf
  Volltext-Extrakt lokal: `notes/cameron_book_chapter5.txt` (1213 Zeilen)
  Strukturierte Bullets: `01_strategy_breakdown/book_notes.md`
- Buch-Neuauflage *How to Day Trade: The Plain Truth* (2024) — Goodreads/Amazon
  (nicht frei verfügbar; nur kriminiltrading.com Summary genutzt)

## YouTube-Video-Transkripte (über pickscribe.com extrahiert)

- **B81TMhUpz50 — 27 Years of Trading Knowledge in 3hrs and 5mins** (Masterclass, Sep 2024)
  Volltext-Transkript lokal: `notes/transcripts/B81TMhUpz50_27years_masterclass.txt` (36.497 Wörter)
  Strukturierte Bullets: `01_strategy_breakdown/masterclass_notes.md`
  Aktuellste komprehensive Strategie-Darstellung — Quelle für viele neue Constraints.
- **jfe1Zl-5EQI — Reversals Class 4 of 12** (31 min, 659k Views) — https://youtu.be/jfe1Zl-5EQI
  Volltext: `notes/transcripts/jfe1Zl-5EQI_reversals_class4.txt`
  Strukturiert: `02_setups/reversals.md`
- **BaZ4R2ovI9k — Level 2 Live Examples** (27 min, 329k Views) — https://youtu.be/BaZ4R2ovI9k
  Volltext: `notes/transcripts/BaZ4R2ovI9k_level2_live.txt`
  Strukturiert: `02_setups/level2_reading.md`
- **KzVbXzkoZkA — Adding to Winners / Scaling Strategies** (1h37m) — https://youtu.be/KzVbXzkoZkA
  Strukturiert: `02_setups/scaling.md`
  Lieferte: 3k-Block-System, Block-Progression, Scaling-Anforderungen, Sell-Half-Hotkey,
  4-Signale-Continue-Adding, "Correct Exit Feels Too Soon", Trade-Around-Core-Variante,
  Hot/Cold-Market-Scaling-Anpassung.
- **afNhgCc-LCw — Ultimate Guide to Trading a Short Squeeze** (1h33m) — https://youtu.be/afNhgCc-LCw
  Strukturiert: `02_setups/parabolic_squeeze.md`
  Lieferte: Parabolic-Momentum als Stock-Type, Sympathy-Momentum-Regel,
  News-vs-No-News-Halt-Risiken (T12), Reverse-Split-Mechanik im Detail,
  BPTH-Hedge-Fund-Disaster-Story, Halt-Pinning-Anti-Pattern,
  Order-Routing-Hard-Rules (kein Market-Order, +15¢-Offset),
  vollständiges Hotkey-Schema (Shift+N, Ctrl+Z/L/K),
  Lightspeed > TD Ameritrade Empfehlung.
- **W3jXQlgGbBc — Sub-VWAP-Trap** (~25min) — https://youtu.be/W3jXQlgGbBc
  Strukturiert: `02_setups/sub_vwap_trap.md`
  Lieferte: kompletten Sub-VWAP-Trap als formales Setup, VWAP-Berechnung,
  "Defended Levels"-Konzept, "20k-Buyer-Ambiguität" (Whale vs Bait),
  "Devil Horns" als Bearish-Rejection auf 1m, Liquidity-Distribution-Insight
  (Vacuum oberhalb VWAP nach Break = schnelle Moves), Live-Trade-Beispiel SCNI.
- **PjVivCcM1B0 — Pre-Market & After-Hours Trading** — https://youtu.be/PjVivCcM1B0
  Strukturiert: `02_setups/premarket_afterhours.md`
  Lieferte: vollständige Order-Mechanik in Extended Hours, Broker-spezifische
  Setup-Anweisungen (Lightspeed/TOS/Webull), HFT-Light-Switch-Verhalten,
  Camerons Cameron-Performance-Daten (After-Hours nicht profitabel), Hard-Order-Rules.
- **JMcaRfFThmg — How to Day Trade Breaking News** — https://youtu.be/JMcaRfFThmg
  Strukturiert: `02_setups/breaking_news.md`
  Lieferte: kompletter Breaking-News-Workflow, "wait for response, don't search news",
  Float-Exception-Regel (NBEV-Beispiel mit 71M Float), LULD-Detail (15s Bid-Hold),
  Stop ~7-8c Norm für Breaking-News, "self-fulfilling prophecy" Reaktionslogik.
- **1FKu4LH0Xss — How to AVOID False Breakouts** — https://youtu.be/1FKu4LH0Xss
  Strukturiert: `02_setups/avoid_false_breakouts.md`
  Lieferte: **5-Indikator-Checkliste** für False-Breakout-Erkennung (Hauptfilter!),
  Front-Side vs Back-Side-Konzept, Algo-Spike+Flush-Mechanik im Detail,
  Whale-at-Resistance-Pattern, Marketmaker-Mathematik (Spread × Volume),
  emotionale Trigger-Words zur Spiral-Selbst-Erkennung.
- **DgWn3egDGb0 — Bollinger Bands Trading Strategy** — https://youtu.be/DgWn3egDGb0
  Strukturiert: `02_setups/bollinger_bands.md`
  Lieferte: vertiefte Bollinger-Anwendung (20/2.0, NICHT ändern, self-fulfilling prophecy),
  Reversal-Confirmation-Workflow (candle-over-candle), 4-stufige Target-Sequenz
  (9EMA→20EMA→VWAP→BB-midline), Compression-then-Expansion-Pattern,
  Cameron's Lifetime-Win-Rate 68% bestätigt, "Essential Indicators"-Liste.
- d0wt45LbvWo — How to WIN at Day Trading as a BEGINNER (2025) — https://pickscribe.com/v/d0wt45LbvWo
- m5zu_X-_51I — How I Made $1,000,000 in 51 Days — https://pickscribe.com/v/m5zu_X-_51I
- 3rEakODkiEg — Ultimate Beginner's Guide to Trading — https://pickscribe.com/v/3rEakODkiEg
- mfGQr2tHoX0 — How I Nailed Trading with the MACD — https://pickscribe.com/v/mfGQr2tHoX0
- eTUYXkAr6Pc — Ultimate Moving Average Trading Guide — https://pickscribe.com/v/eTUYXkAr6Pc
- HYoQYCBW4sw — Master This ONE Candlestick Pattern — https://pickscribe.com/v/HYoQYCBW4sw
- iS5lvJGMM8E — Ultimate RSI Trading Strategy — https://pickscribe.com/v/iS5lvJGMM8E

Aufbereitete Bullets pro Video: `01_strategy_breakdown/video_notes.md`.

## Sekundärquellen / Aufbereitungen

- 5 Pillars Filter (TradingView-Skript) — https://www.tradingview.com/script/mbqMf3pF-Ross-Cameron-5-Pillars-Filter/
- Ross Cameron Day Trading Guide 2025 (Quizlet-Karten) — https://quizlet.com/1003675244/ross-cameron-day-trading-guide-2025-flash-cards/
- Buch-Summary "How to Day Trade: The Plain Truth" — https://kriminiltrading.com/blogs/...
- Shortform PDF Summary — https://www.shortform.com/pdf/how-to-day-trade-pdf-ross-cameron
- Bullishbears Review — https://bullishbears.com/warrior-trading-review/
- Speedtrader 5 Lessons — https://speedtrader.com/5-day-trading-lessons-from-ross-cameron-of-warrior-trading/

## Was NICHT in den Constraints steht (bewusst weggelassen)

- Tape-Reading-Heuristiken (Level 2 / Time & Sales) — diskretionär, schlecht mechanisierbar.
- "Gefühl für den stärksten Stock" — durch Scanner-Score approximiert, nicht 1:1 übersetzt.
- Spezifische DAS-Trader-Hotkey-Konfigurationen — Plattform-Detail, irrelevant für Logik.
- Trading-Psychologie / Routinen — separat zu modellieren, nicht Teil der Strategie-Constraints.

## Bekannte Konflikte / Bandbreiten in der Literatur

| Größe              | Werte in der Literatur                | Wahl in constraints.yaml |
|--------------------|---------------------------------------|--------------------------|
| Float Maximum      | 10M (5 Pillars) vs. 20M (älter)        | strict 10M, loose 20M    |
| RVOL Minimum       | 2x (allg.) vs. 5x (5 Pillars)          | 5x (Cameron-aktuell)     |
| Daily % Change     | 4% (Gap-and-Go) vs. 10% (5 Pillars)    | 10% strict, 4% Gap       |
| Preisrange         | $1-$20 vs. bis $100 in Ausnahmen       | strict $1-$20            |
| Premarket Volume   | 100k–1M je nach Quelle                 | 300k Min, 1M safe        |
| Daily Max Loss     | nicht öffentlich beziffert (skaliert)  | $1500 als Default        |
