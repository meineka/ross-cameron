# Pilot-Backtest Final Report
Stand: 2026-05-09 · ~55 Tage Data · 1.026 Ticker-Tage · yfinance 5-min

## Executive Summary

| Metric | Pilot | Cameron-Benchmark | Status |
|---|---|---|---|
| Trades | 604 | n/a | ✓ ausreichend |
| **Win-Rate** | **62,6 %** | 68 % (lifetime), 71 % (winning days) | ✓ **plausibel nahe** |
| Avg Winner / Share | 32,1 ¢ | 11 ¢ (Live) | ↗ höher (5m-Bars vs 1m) |
| Avg Loser / Share | −38,0 ¢ | −8 ¢ (Live) | ↗ höher (5m-Stops weiter) |
| **Realized R/R** | **0,39** | 2,0+ Ziel | ✗ **schwach** |
| Total P&L / Share | +35,29 ¢ über 604 Trades | n/a | ✓ profitabel kumulativ |

**Erste Aussage: Camerons Bull-Flag ist auf 5-min-Bars detektierbar und produziert positive Edge — aber R/R liegt unter dem 2:1-Ziel.**

## Pipeline-Status

| Schritt | Status | Output | Laufzeit |
|---|---|---|---|
| 1. Universe-Pull (NASDAQ-Trader CSV) | ✓ | 6.865 Tickers | 3 s |
| 2. Daily-Bars (4 Mon, yfinance batch) | ✓ | 569k rows | ~4 min |
| 3. Cameron-Candidate-Filter | ✓ | 3.852 Paare, 1.589 Tickers | <1 s |
| 4. 5-min Bars (Candidates innerhalb 55 Tage) | ✓ | 774k rows, 1.026 Ticker-Tage | ~17 min |
| 5. EDGAR 8-K Catalyst-Sample | ✓ | 50 getaggt | ~3 min |
| 6. Bull-Flag-Detector + Exit-Sim | ✓ | 604 Trades | 4:30 min |

**Total Daten-Volumen lokal:** 23 MB Parquet · alles `$0` · komplett offline lauffähig.

## Trade-Verteilung nach Exit-Grund

| Exit | Anzahl | Anteil | Bedeutung |
|---|---|---|---|
| `target2_hit` | 190 | 31,5 % | voller Pole-Height-Move erreicht |
| `stop_hit_after_T1_BE` | 181 | 30,0 % | 50 % bei T1 raus, Rest auf BE-Stop = ~0,5R |
| `stop_hit` | 217 | 35,9 % | voller Loser, T1 nicht erreicht |
| `eod_exit` | 16 | 2,6 % | end-of-day fallback |

→ **35,9 % volle Loser** drückt das R/R. Camerons Echt-Strategie verhindert das mit:
- Tape-Reading-Confirmation **vor** Entry (im Backtest fehlt)
- 5-Indikator-False-Breakout-Filter (im Backtest fehlt)
- Catalyst-Verifikation pro Trade (im Backtest fehlt)

## Identifizierte Limitierungen

### Model-Approximationen (im Pilot bewusst weggelassen)
1. **MACD-Confirmation** vor Entry — im Detector nicht aktiv
2. **5-Indikator-False-Breakout-Filter** (constraints.yaml#false_breakout_filter) — nicht angewandt
3. **Catalyst-Filter pro Trade** — nur Daily-Filter, kein per-Trade-News-Check
4. **Tape-Reading-Heuristiken** — nicht modellierbar ohne Level-2-Daten
5. **VWAP-Session-Reset** — aktuell cumulative seit Datenstart, sollte je Trading-Tag neu

### Daten-Limitierungen
1. **5-min statt 1-min** — yfinance-Cap. Cameron's Micro-Pullback nicht echt darstellbar.
2. **55 Tage Lookback** — yfinance-Cap. Sample-Size kompakt.
3. **Yahoo-Volume-Quellen** — nicht 100 % SIP-konsolidiert, RVOL-Proxy daher Approximation.
4. **News-Coverage**: nur SEC 8-K, keine Tweets/PR-Newswires.

### Identifizierte Bugs / TODOs
- [ ] **VWAP-Session-Reset** pro Trading-Tag (aktuell falsch cumuliert)
- [ ] **Slippage-Modell** für Stops realistischer (1-2¢ schlechter als low)
- [ ] **Pre/After-Market-Filter** in Daily-Cameron-Filter (aktuell nimmt RTH-Closes)
- [ ] **EDGAR-Tagging auf alle Candidates** (nicht nur Sample)
- [ ] **MACD + 5-Indicator-False-Breakout-Filter** in Detector einbauen

## Mathematik-Check (Plausibilität)

```
Pole 5 % auf $5-Stock → Move 25¢
Stop = Flag-Low ≈ 25 % Retrace = ~6¢ unter Entry
T1 = +6¢ ≈ 1R
T2 = +25¢ ≈ 4R

Expected Win-Rate (theoretisch, alle T1 erreichen):
  - 50 % T1-Touch * 50 % T2-Reach = 25 % full T2
  - 50 % T1-Touch * 50 % BE-Stop = 25 % half-profit  
  - 50 % no T1, full-stop = 50 % loss

Pilot-Result: 31,5 % T2 + 30 % T1+BE + 35,9 % full-stop
→ besser als naive 50/25/25-Verteilung
→ Pattern-Detector hat tatsächlich Edge
```

## Win-Rate vs Cameron — Diskussion

Pilot 62,6 % ist nahe Camerons 68 % Lifetime — **bestätigt erste Edge**.
Aber: das ist OHNE Tape-Reading, OHNE False-Breakout-Filter, OHNE Catalyst-Match.
Cameron's eigentliche Edge kommt aus **Filter-Stack der Bad-Trades vorher rausschmeißt**.

Erwartung bei voller Filter-Anwendung:
- Win-Rate: 65–70 % (näher an Camerons 71 % Winning-Days)
- Avg-Loser: kleiner (False-Breakout-Filter eliminiert worst losers)
- R/R: in Richtung 1,0 wenn nicht 2,0

## Konkrete Schlussfolgerungen

✓ **Pipeline funktioniert** end-to-end auf $0-Stack
✓ **Pattern-Detector findet echte Cameron-Setups** (Win-Rate plausibel)
✓ **Performance OK** (4:30 min für 1.026 Ticker-Tage, 0,6s/Ticker)
✓ **Daten-Stack reicht** für Pilot-Validation der Strategie

✗ **R/R-Realisierung schwach** — 0,39 statt 2,0 Cameron-Ziel
✗ **Filter-Stack fehlt** — die wichtigste Edge-Komponente

## Empfehlung Nächste Iteration

**Priority 1 — Detector aufrüsten** (1–2 Tage Code):
1. MACD 12/26/9 berechnen, Cross-Down als Veto
2. False-Breakout-Filter (5 Indikatoren) — SKIP wenn ≥2 Treffer
3. VWAP-Session-Reset
4. Slippage 1¢ auf jeden Stop
→ Erwartung: Win-Rate ↑, full-stop-Anteil ↓

**Priority 2 — Bessere Datenbasis** (1 Monat Polygon $79):
1. 1-min Bars für echte Cameron-Granularität
2. SIP-Volume-Daten für sauberes RVOL
3. 5+ Jahre History → 100x mehr Trades = signifikante Stats

**Priority 3 — Live-Validation** (nach Backtest-Edge bestätigt):
1. Alpaca Paper-Account
2. Real-time-Streaming via Polygon-WebSocket
3. 30 Tage Paper, dann Mikro-Real

## Files
- [bootstrap.py](bootstrap.py) — Daten-Pull
- [validate.py](validate.py) — Sanity-Check
- [backtest_bull_flag.py](backtest_bull_flag.py) — Detector + Exit-Sim
- [data_pilot/trades.parquet](data_pilot/trades.parquet) — alle 604 Trades als DataFrame
- [data_pilot/candidates.parquet](data_pilot/candidates.parquet) — 3.852 Cameron-Candidate-Days

## Reproduzieren

```bash
cd ross-cameron/04_backtest
python bootstrap.py     # ~25 Min für Daten-Pull (einmalig)
python validate.py      # Sanity-Check
python backtest_bull_flag.py            # Full-Run
python backtest_bull_flag.py --limit 5  # Quick-Test
python backtest_bull_flag.py --ticker VNRX  # Single-Ticker
```
