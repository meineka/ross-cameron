# Level 2 / Tape Reading — Konkrete Mechanik

Quelle: YouTube `BaZ4R2ovI9k` — "How to use Level 2 data in your Day Trading Strategy"
(27 min, 329k Views) — ersetzt das fehlende Buch-Kapitel 7.
Volltext: `notes/transcripts/BaZ4R2ovI9k_level2_live.txt`
Live-Beispiel: KRTX, +270 % am Tag, Squeeze von $30 auf $65.

## Was Level 2 zeigt
- **Bid-Seite (links)**: Käufer mit Limit-Buy-Orders
- **Ask-Seite (rechts)**: Verkäufer mit Limit-Sell-Orders
- **Time & Sales**: jede ausgeführte Transaktion in Echtzeit
- **Spread**: Differenz zwischen bestem Bid und bestem Ask

## Die "Hidden Buyer"-Erkennung (Kern-Insight des Videos)

**Pattern**: Sustained Selling auf einem Preis-Level, aber Bid hält trotzdem.

```
Beispiel (KRTX): Preis-Level $54
  - Visible Quote: nur 400-500 Shares Bid
  - T&S zeigt: 30k+ Shares Sells gehen durch
  - Preis bricht $54 NICHT
  → Hidden Buyer akkumuliert unsichtbar (Iceberg-Order)
```

**Interpretation**:
- Jemand kauft große Position auf, ohne sie im Order-Book zu zeigen
- Iceberg- oder verstecktes Limit-Order-Routing
- Typisch für institutionellen Käufer (Hedge Fund, Whale)
- **Trading-Signal**: NICHT short gegen einen Hidden Buyer
- **Confirmation Long**: wenn Stock sustained Niveau hält + breakt nach oben → Trade Long

## Die "Why-would-they-cover-here?"-Logik

Beim Identifizieren ob ein Hidden Buyer ein Whale (Akkumulation) oder ein Short-Coverer ist:
- **Wenn Stock weak aussieht** (Gravestone Doji + high red volume), aber jemand kauft groß:
  - Short-Coverer würde **nicht hier** covern — würde auf Move-Down warten (Profit maximieren)
  - Daher: das ist Akkumulation, nicht Cover → bullish Long-Signal
- **Wenn Stock long-extended** und jemand kauft groß:
  - Wahrscheinlicher Short-Coverer der margin-called wird → kann Squeeze auslösen
  - Dann **Momentum-Long** mit dem Whale, nicht gegen ihn

## Wichtige Schwellen aus dem Video

- **Big Seller / Big Buyer Schwelle**: 100k+ Shares auf einer Seite (siehe constraints.yaml)
- **"Massive Position"**: 50k–500k Shares
- **Spread-zu-Preis**: bei $50+ Stocks oft $0.75–$1.90 Spread
  → "Out of wheelhouse" für Small-Account-Trader
- **Long Investor "Tell"**: Stock ignoriert Standard-TA (z.B. red doji + high vol → keine Umkehr)
  → Hinweis auf Long-Term-Buyer der Charts ignoriert

## Circuit-Breaker-Halt-Mechanik (aus dem Video bestätigt)

- **Trigger**: Preis bewegt sich > 10 % in 5 Minuten
- **LULD-Level** (Limit Up / Limit Down) = Halt-Schwelle (z.B. $54.57 als LULD)
- **Hold-Requirement**: Bid muss 15 Sekunden über/unter LULD halten, sonst Halt
- **Resume-Verhalten**: 
  - Halt-Up: oft Resume auf höherem Preis
  - Halt-Down: oft Resume auf niedrigerem Preis
- **"Chasing Circuit Breakers"** ist Camerons Anti-Pattern: Stock pendelt um LULD ohne klaren Move → nicht traden

## Cameron's "Wheelhouse"-Regel (NEU)

- "Stay in your wheelhouse, leave the rest"
- Cameron's Small-Account-Wheelhouse: **$3–$8 Stocks**
- Bei höheren Preisen: zu großer Spread, zu viel Risk pro Position
- **Disziplin**: gute Setups bei zu teurem Stock = Skip, kein FOMO
- "There will always be another stock around the corner"

## Tape-Reading als Confirmation

Standard-Workflow:
1. Chart-Pattern bestätigt sich (z.B. First-Candle-New-High Pullback)
2. Level 2: prüfe Spread (akzeptabel?)
3. T&S: warte auf **burst of green orders** (Buy-Side-Aggression)
4. Erst dann Entry — vermeidet False-Breakouts

## Konkrete Anti-Patterns aus dem Video

- Stock at extreme (höchster ATH) **mit großen Spreads** → kein Entry
- Mehrfache LULD-Halts in Folge → wahrscheinlich keine sauberen Setups
- Long-Investor-Akkumulation **erkannt aber bereits weit gelaufen** → Edge bereits weg
- Versuch zu shorten gegen einen klaren Hidden Buyer
