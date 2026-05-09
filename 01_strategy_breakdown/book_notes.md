# Buch-Notes: "How to Day Trade" (Ross Cameron, 2015 / Neuauflage 2018)

Stand: 2026-05-09. Quelle: offizieller PDF-Auszug von warriortrading.com,
Datei `5CH_How_To_Day_Trade_Ross_Cameron_Warrior_Trading.pdf` (1.1 MB,
extrahierter Volltext: `notes/cameron_book_chapter5.txt`, 1213 Zeilen).

**Achtung:** Das PDF enthält nur **Kapitel 1–5** (Sample). Kapitel 6–11
(Support/Resistance, Order Types/Level 2/Hotkeys, Momentum Strategies,
Counter Trend Strategies, Stock Scanning, 3-Step Plan) fehlen im PDF und
müssten separat beschafft werden (Vollbuch oder offizielle Auszüge).

---

## Kapitel 1 — Why Most Traders Fail

- 1 von 10 Tradern wird profitabel — "9 out of 10 traders" verlieren
- "You can lose 50% of the time and still make money" wenn P/L-Ratio stimmt
- "Best traders are great losers" — Ziel: schlechte Trades schnell beenden
- **Tracking Pflicht**: jeden Trade in Excel mit Setup / Time / Symbol / Price / Entry / Exit / P&L / Notes
- Disziplin als Muskel: **30 min Sport + 15 min Meditation täglich** (Camerons eigene Routine)

### Profit/Loss-Ratio-Tabelle (verbatim aus dem Buch)
| P/L-Ratio | Breakeven-Accuracy (vor Commissions) |
|-----------|--------------------------------------|
| 2:1       | 33 %                                 |
| 1:1       | 50 %                                 |
| 1:2       | 66 % (unsustainable)                 |

---

## Kapitel 2 — Risk Management

- Ziel-Konto-Performance: $25k Konto → $50k+/Jahr (200 % p.a.)
- $25k Min für PDT (US-Day-Trading), 4× Margin = $100k Buying Power
- 5 % Tagesreturn auf $100k Margin = 20 % auf $25k Cash
- **Drei Risiko-Typen** (im Buch genannt):
  1. Distance Entry → Stop (Hauptrisiko)
  2. Volatility Risk (Marktphasen)
  3. Exposure Risk (Position $-Wert)
- "Avoid trading during peak volatility right after news — wait for the dust to settle"
- **Stock Halts**:
  - Circuit Breaker: ±10 % in 5 Min → 5-Min-Halt
  - News-Halts: bis Pressemitteilung
  - SSR (Short Sale Restriction): Stock fällt > 10 % vs. Vorabschluss → Shorts nur bei Up-Tick
  - Penny Stocks: Halt-Risiko durch SEC-Investigations
- **Max Loss Rules**:
  - max-loss-per-day = daily-profit-target (symmetrisch — Buch-Verbatim)
  - max-loss-per-trade ≈ **25 %** des daily-goals
  - Wenn Tag-Max-Loss erreicht: **Computer aus, weggehen**
  - Bei Schwierigkeit, Stop manuell zu drücken: automatische Stop-Orders setzen
- **Risk Balancing**:
  - Niemals einen Trade so groß, dass er alle vorherigen Gewinner zerstören kann
  - Risk pro Trade über den Tag in **kleinen Schritten** anpassen ($100 → $150/$75, nicht $100 → $1000)
  - Vermeidet "Revenge Trading"
- **"Add to Winners, NOT to Losers"** — explizite Regel:
  - Loser: nicht nachkaufen (Cost-Basis-Verbilligung erhöht Verlust)
  - Winner: nachkaufen erlaubt; neuer Cost-Average → BE-Stop
- Best trades **arbeiten fast sofort** (in deine Richtung). Wenn nicht: oft Loser.

---

## Kapitel 3 — Stock Selection

### Volumen
- Avg Daily Volume Schwelle als Hinweis: 500k/Tag — wenn 2M heute = "above average interest"
- RVOL = today/avg → "1 = average, 2 = 2× = interesting, anything above 2 is worth a look"
- **Wichtig**: Im Buch (2015) ist RVOL ≥ 2 die Schwelle. In Camerons späteren Videos (2024+) ist es **5×**. → Strategie hat sich verschärft.

### Catalyst
- "Hunt for a Catalyst" jede Frühe
- Stärkste Quellen: Company-PR, Earnings, Sales Contracts, Buyout Offers, Buyback-Programs, FDA, Patent, Government Investigations, Top-Tier-Analysten
- Schwache/verdächtige Quellen: unbekannte Analysten, unbestätigte Gerüchte → skip
- **Buyouts NICHT traden** — Preis ist fixed am Deal-Preis, kein Edge
- "Follow the chart, not the fundamentals" — auch beste News kann schlechte Price Action geben

### Float
- Buch-Schwelle (älter): **< 50M** für Momentum-Trades
- Sweet Spot für 100 %+ Moves: **< 10M**
- Bei 5M-Float können > 20M Shares pro Tag traden = **4× Float Rotation**
- "Stocks that move 100–200 % intraday almost always have at least one full float rotation"
- **Reversal-Trades**: Float egal (any float OK)
- > 500M Float: zu wenig volatil, wenig interessant

### "Former Runners"
- Stocks mit historischen 100 %+ Moves
- Wenn so ein Stock News bekommt: Watchlist-Top-Priorität
- "We already know it has the potential — under the right conditions it can move again"

### Follow-Through Days vs. Inside Days
- **Follow-Through Day** = neue Kerze bricht über High des Vortages = Momentum-Continuation, gut
- **Inside Day** = Kerze ist komplett innerhalb der Vortagesrange = nicht traden, kein Edge
- "Follow-through brings new buyers + forces shorts to cover"

### Intraday Extremes
- Mid-day News-Spikes mit Volatility-Halt → Watchlist-Trigger
- Sogar **ohne** klaren Catalyst tradebar, wenn RVOL hoch ist
- "We prefer a clear catalyst, but we don't discount stocks moving on heavy volume even without one"

---

## Kapitel 4 — Candlestick Patterns

- 4 Datenpunkte pro Kerze: Open / High / Low / Close
- **Doji** (kleiner Body, beide Seiten Wick): Indecision → bei top eines 5–10-grüne-Kerzen-Moves = Reversal-Signal
- **Topping Tail**: langer oberer Docht am Top eines Trends → Reversal-Signal
- **Bottoming Tail**: langer unterer Docht am Boden eines Downtrends → Reversal-Signal
- **Hammer**: kleiner Body + lange unter Wick am Boden → "hammering out a new base"; Confirmation = nächste Kerze bricht Hammer-High ("candle over candle")
- **Inverted Hammer**: kleiner Body + lange obere Wick am Top eines Uptrends → Indecision/Reversal
- **Long Body Green Candle**: starkes Bullish-Sentiment → "always want to be holding when a long body candle forms; **never buy AFTER one**" (Chasing-Falle)
- **Long Body Red Candle**: stark bearish; nach 3 langen roten in Folge → Bounce-Wahrscheinlichkeit erhöht

---

## Kapitel 5 — Indicators

### Chart-Layout
- **Drei Charts gleichzeitig**: 1m, 5m, Daily
- 1m + 5m: nur heutige Price Action
- Daily: 3–6 Monate Historie, horizontale Trendlinien
- "Keep charts clean. Avoid indicator-clutter."

### Moving Averages — Camerons exakte Wahl
- **9 EMA** (gray): primärer Trail-Support intraday
- **20 EMA** (blue): sekundärer Support
- **50 EMA** (red): Daily-Chart-Trend
- **200 EMA** (purple): Major-S/R, Trend-Definition
- **EMA over SMA** — schnelle Reaktion auf neue Price Action wichtiger als Glättung

### MA-Trading-Regeln (verbatim)
- Stock > MA = Uptrend, Stock < MA = Downtrend
- "Trending stocks usually respect either 9 EMA or 20 EMA. **I prefer 9 EMA — shows more strength**"
- Pattern: Quick-Move-Up → Sideways-Konsolidierung → Tap 9 EMA → Up
- **Buy 1. + 2. MA-Pullback aggressiv, ab 3. vorsichtig** (im Buch — in Videos geschärft zu "3. nicht traden")
- Stop: knapp **unter 9 EMA**, mitziehen wenn Preis steigt
- Reversal-Trade: Stock **extended weg** vom 9 EMA, Ziel = Rückkehr zu 9 EMA
- **200 EMA**: starker Reversal-S/R
- "I rarely take long trades **below 200 EMA** or short trades **above 200 EMA**" — harte Regel

### VWAP
- Equilibrium-Preis des Tages, volume-weighted
- Trend-Trades: Entries **nahe VWAP** suchen
- Wenn Preis stark extended: stattdessen 9 EMA als Referenz
- Counter-Trend: stark extended **weg von VWAP** = Setup

### Bollinger Bands
- Settings: **20 Period, 2.0 Stddev** (Default, nicht ändern)
- **Nur 5m Chart** — nicht 1m, nicht Daily
- **Nur für Reversals**, nicht für Trend-Trades
- Kerze außerhalb der Bands = Extrem-Situation = potentieller Reversal-Trigger
- Achtung: Preis kann **lange** außerhalb bleiben in Parabolics — nicht zu früh einsteigen

### RSI
- Settings: **14 Period** (Default)
- "RSI < 20 oder > 80 = intraday extreme"
- Best Reversals: **RSI < 5 oder > 95**
- "RSI alone is not a reliable buy/sell — needs confirmation"
- Cameron nutzt RSI nicht **auf** dem Chart, sondern als **Scanner-Filter** für Reversal-Kandidaten

### Volume Bars
- **Kritisch — kein Trading ohne**
- Decreasing Volume in einem Trend = Trend-Wechsel-Risiko
- Konsolidierung mit Volume-Spike = potentieller Breakout

---

## Was im PDF fehlt (Kapitel 6–11)

- Kapitel 6 — Support/Resistance (Linien-Zeichnung, Levels-Identifikation)
- Kapitel 7 — Order Types, Level 2, Time & Sales, Hotkeys (DAS-Trader-Praxis)
- Kapitel 8 — **Momentum Trading Strategies** (das Hauptkapitel mit Bull-Flag, Pullback, Gap & Go in Buch-Tiefe)
- Kapitel 9 — **Counter-Trend Strategies** (Reversal-Setups Bollinger/RSI in Detail)
- Kapitel 10 — Stock Scanner Setup
- Kapitel 11 — 3-Step Day Trading Plan (Camerons Routine)

→ Wenn du diese willst: Vollbuch über Goodreads/Amazon, oder Camerons Updated Edition
*"How to Day Trade: The Plain Truth"* (2024). Beide sind **nicht** als legaler Volltext frei verfügbar.

---

## Konfliktauflösung Buch (2015) vs. Videos (2024)

| Constraint              | Buch                  | Videos               | Strategie hat sich entwickelt → |
|-------------------------|-----------------------|----------------------|---------------------------------|
| Float Maximum           | < 50M (loose < 10M)   | < 20M (strict < 10M) | Schwellen wurden enger           |
| RVOL Minimum            | > 2× ("interesting")  | > 5×                 | Selektivität gestiegen           |
| Pullback-Zähl-Regel     | "1./2. aggressiv, 3. vorsichtig" | "3. NICHT traden" | Strikter geworden    |
| Tagesfenster            | nicht beziffert       | 7–11 ET              | Konkretisiert                    |
| Bollinger Bands         | nur 5m, Reversals     | bestätigt in V7     | Konstant                         |
| RSI                     | Scanner-Filter        | Reversals only       | Konstant                         |

→ **Empfehlung**: Die strikten Werte aus den Videos in `constraints.yaml`
benutzen (das ist Camerons aktuelle Praxis), das Buch als historischen
Kontext und für die "weichen" Konzepte (Disziplin, Add-to-Winners,
Former Runners, Inside-vs-Follow-Through-Days) lesen.
