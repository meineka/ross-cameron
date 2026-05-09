# Offene Fragen

Stand 2026-05-09 nach Abschluss der Tier-1-Recherche.

## Kern-Entscheidung (blockt alles weitere)

**Zielmarkt**: US Small Caps (echte Cameron-Replik) ODER Übertragung auf MT5?

| | US Small Caps original | MT5-Übertragung |
|---|---|---|
| Edge-Erhalt | 100 % (Camerons Stats gelten 1:1) | unklar (Float-Filter sinnlos) |
| Daten-Aufwand | hoch (Polygon/IBKR + Float + News-API) | niedrig (du hast schon MT5-Daten) |
| Setup-Anwendbarkeit | alle 11 Setups direkt | Reversals + Bull Flag + Breaking News (Indizes) |
| Account-Anforderung | $25k PDT-Min für Day-Trade | bestehend |
| Realismus Cameron-Replik | hoch | niedrig (anderer Markt) |

→ Entscheidung steht aus. Beeinflusst direkt die Datenquelle, das Backtest-Design,
   und welche YAML-Constraints überhaupt aktiv werden können.

## Datenfragen (abhängig von Markt-Entscheidung)

- 1m-Bars für US-Equities mit Premarket inkludiert? (Polygon, IBKR, Alpaca)
- Float-Daten in real-time (für 5-Pillars-Filter)? (FinViz, IEX Cloud, Polygon)
- News-Catalyst-Feed mit Tier-1-Quellen? (Bloomberg API teuer, Benzinga ok)
- Realistische Slippage-Modellierung bei Low-Float-Stocks?
- Halt-/LULD-Daten historisch verfügbar?

## Modellierungs-Fragen

- Wie modellieren wir "stärkster Stock am Tag" als Scanner-Score?
  → Vorschlag: composite_score = z(RVOL) + z(daily_%) + 1{news} + 1{float<20M}
- Wie viele Setups parallel? Vorschlag: erst Bull Flag sauber, dann erweitern.
- Tape-Reading-Heuristiken (Hidden-Buyer-Detection) im Backtest sinnvoll?
  → Nur wenn 1-Sekunden-Tape-Daten vorhanden, sonst skippen.
- Spiral-Mechanik (Camerons Live-Stats: 71 % grün vs 50 % rot Acc):
  modellieren wir trader-state-machine? → Erstmal weglassen (Komplexität).

## Strategie-Fragen

- "Stärkster Stock"-Diskretion: kann ein Algo alle 5 Pillars + Bonus-Criteria
  als Ranking ausrechnen und Top-1 picken? → Ja, aber Tape-Reading-Filter fehlt.
- Quarter-Size-Rule: per-Tag-State (intraday cumulative profit per share)
  oder per-Trade? → per-Tag, mit Reset um 09:30 ET.
- Red-Streak-Rule: zählt Calendar-Days oder Trading-Days? → Trading-Days.

## Pragma-Vorschlag (wenn Entscheidung steht)

Wenn **US Small Caps**:
1. Polygon-API-Account, 1m-Bars + Snapshot-API für Float
2. Benzinga-Newsfeed integrieren
3. Backtest-Engine: für jeden Tag pre-market Watchlist via 5-Pillars filtern
4. Pro Stock: Bull-Flag-Detector laufen lassen
5. Vergleich Backtest-Stats vs Cameron-Benchmarks

Wenn **MT5-Übertragung**:
1. Float-Filter und News-Catalyst entfallen → andere "5 Pillars" definieren
2. Z.B. NDX100, GER40, BTC: tägliche %-Gainer als Watchlist
3. Bull-Flag-Detector identisch
4. Reversal + Sub-VWAP-Trap als sekundäre Setups
5. Aber: Edge unklar, Cameron's Win-Rates nicht 1:1 erwartbar
