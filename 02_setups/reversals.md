# Setup: Reversals (Top + Bottom Bounce)

Quelle: YouTube `jfe1Zl-5EQI` — "Day Trading Strategy (reversals) Class 4 of 12"
(31 min, 659k Views) — diese Klasse ersetzt das fehlende Buch-Kapitel 9.
Volltext-Transkript: `notes/transcripts/jfe1Zl-5EQI_reversals_class4.txt`

## Kern-Idee
Stocks bewegen sich oft zu Extremen ("Rubber Band stretched"). Reversal-Trades
gehen GEGEN diese Extremebewegung — Long am Bottom-Bounce, Short am Top.
"What goes up must come down. Bulls take the stairs, bears take the window."

## Pflicht-Bedingungen (alle gleichzeitig)

### Pre-Conditions auf 5m-Chart
- **Stretched move**: 5–10 aufeinanderfolgende Kerzen in einer Richtung
- **Volume**: min 500k Shares, präferiert ≥ 1M
  - **Best Signal**: Volume PEAKS am Bottom des Selloffs (high-of-day-volume auf finaler roter Kerze)
- **RSI Extrem**:
  - Long-Bottom-Bounce: RSI < 10 (Hauptkriterium; 14-Periode SMA)
  - Short-Top-Reversal: RSI > 90
- **Bollinger Bands** (20-Period, 2.0 stddev): finale Kerze **vollständig außerhalb** der Bands
- **Pin Bar / Doji** als finale Kerze:
  - Bottom-Bounce: lange untere Wick (Bottoming Tail / Hammer)
  - Top: lange obere Wick (Topping Tail / Shooting Star)
  - Pin Bar = Wick **länger als Body**

### Bonus: Daily-Chart-Support / Resistance an gleicher Stelle
- z.B. bei Long-Bounce: Daily-Support-Level + niedriges RSI = sehr starkes Setup
- Whole-Dollar-Levels ($21, $43) als zusätzlicher Confluence-Anchor

## Entry-Trigger

```
Bottom-Bounce Long:
  Trigger = first 5m candle to make a new HIGH after consecutive red 5m candles
  Confirmation = green orders bursting through Time & Sales
```

**Wichtig**: Cameron präferiert **5m über 1m** für Reversal-Entries — 5m ist sauberer.
1m nur dann nutzen, wenn:
- 5m hat 5+ rote Kerzen
- 1m hat **20+** rote Kerzen (= echtes Extrem)

## Stop

- **Primär**: Low of Day (LOD) bei Bottom-Bounce; High of Day (HOD) bei Top-Short
- **Wenn LOD/HOD zu weit weg**: arbiträrer 20–30 ¢ Stop
- **Ausstiegsregel**: "Stock geht 30 ¢ gegen mich → Mistime-Erkennung → raus, neu versuchen"

## Targets

Gestaffelt — Reversal-Trades haben oft **6:1 R/R** auf den besten Setups:
1. Erster Profit-Take: Volume-Weighted Average Price (VWAP)
2. Dann: Moving Averages (9 EMA, 20 EMA)
3. Trailen: Stop unter Low der letzten geschlossenen 5m-Kerze

## Trade-Management

- Bei Profit: Stop sofort auf BE
- Wenn Stock konsolidiert nach Reversal → kann zu Bear-Flag (kein Reversal) oder zu Momentum-Trade (Reversal → durch MA gebrochen → flip Long) werden
- "Sometimes der Reversal-Trade verwandelt sich in einen Momentum-Trade" wenn Stock durch MA-Resistance bricht und Konsolidierung dort holds

## Anti-Patterns (Skip wenn …)

- Stock fällt nur **langsam** über den Tag (kein Rubber-Band-Effekt) — kein Extrem
- RSI nicht im Extrem → kein Setup
- Volume nicht peak am Bottom → unsicher
- Keine klare Pin-Bar/Doji-Bestätigung am Boden

## Cameron's Daily-Cushion-Rule (NEU — aus diesem Video)

- Erste paar Trades morgens: Ziel = $300–400 Cushion aufbauen
- Mit Cushion: kann man etwas mehr Risk pro folgendem Trade nehmen
- **Gründer→Rot-Verbot**: Wenn Cushion vorhanden + man gibt $X zurück, wo Cushion − X > 0 → Stop mit Restprofit, nie ins Minus rutschen
- "Adjust stop on the day to $100 daily profit minimum once you've made $400+"
- Risk-Default: $500 max/Trade, $200 wenn nicht confident
- 1000 Shares typisch bei $500 Risk
