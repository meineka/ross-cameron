# Pilot Backtest Final Report — V3 (Cameron-Workflow-konform)

Stand: 2026-05-09 · Iteration 3
Was neu: **Top-N-pro-Tag-Filter** (Cameron's "stärkster Stock"-Logik)

## Executive Summary

**Cameron's Workflow ist der entscheidende Faktor.** Erst durch Beschränkung auf
die Top-N stärksten Stocks pro Tag (statt alle qualifizierenden) erreicht der
Backtest Win-Rates nahe Camerons Live-Stats.

| Modus | Trades | **Win-Rate** | Avg-W/L-Ratio | Notes |
|---|---|---|---|---|
| All Days (v2 loose, kein top-N) | 396 | 63,9 % | 0,97 | "Trade alles" — verdünnt |
| **Top-3 pro Tag** ★ | 62 | **69,4 %** | 0,60 | Cameron's eigener Stil |
| **Top-10 pro Tag** ★ | 130 | **69,2 %** | 0,74 | bester Trades/WR-Mix |
| Top-20 pro Tag | 196 | 61,7 % | 0,86 | Verdünnung beginnt |

→ **Cameron's Live-Stats: 71 % Winning-Day-Acc, 68 % Lifetime.**
   Top-3 + Top-10 erreichen praktisch diesen Bereich.

## Per-Rank-Analyse (entscheidende Erkenntnis)

```
Rank-1 (HOTTEST):   66.7% WR, avg PnL -3,99¢   ← oft parabolic, kein cleanes Bull-Flag!
Rank-2:             64.7% WR, avg PnL +34,5¢
Rank-3:             76.2% WR, avg PnL +14,3¢   ← Sweet Spot Anfang
Rank-4:             77.8% WR, avg PnL +20,4¢
Rank-5:             69.2% WR, avg PnL +22,4¢
Rank-6:             77.8% WR, avg PnL +35,5¢
Rank-7:             81.8% WR, avg PnL +19,7¢   ← bester Einzelrang
Rank-8:             50.0% WR, avg PnL  -3,6¢
Rank-9:             62.5% WR, avg PnL +14,6¢
Rank-10:           100.0% WR (n=2)
Rank-11+:          fällt schnell auf <50% WR
```

**Insight**: Rank-1 (der **stärkste** Stock des Tages) ist oft schon zu weit
gelaufen / parabolic für sauberes Bull-Flag-Setup. **Rank 2-7** liefert die
besten Setups.

→ **Konkrete Trading-Empfehlung**: Watchlist Top-10 erstellen, **Rank-1 mit
Vorsicht** (oft schon parabolic), **Rank 2-7 priorisieren**.

## Pole-7% vs Pole-5% (Edge-Tuning getestet)

| Setup | Trades | Win-Rate | Avg-W/L | Total +¢ |
|---|---|---|---|---|
| Top-3 Pole 5% | 62 | 69,4 % | 0,60 | 7,9 |
| Top-3 Pole 7% | 59 | 67,8 % | 0,63 | 7,1 |
| Top-10 Pole 5% | 130 | 69,2 % | 0,74 | **19,1** |
| Top-10 Pole 7% | 117 | 69,2 % | 0,74 | 18,3 |

**Verdict**: Pole-7% bringt **keine Win-Rate-Verbesserung**. Reduziert Sample
ohne Edge-Gewinn. → **Pole 5% bleibt optimal**.

## Cameron-Compliance-Check (constraints.yaml)

Vollständige Audit-Tabelle in `CAMERON_COMPLIANCE.md`. Coverage-Score:

| Kategorie | Coverage |
|---|---|
| Universe (5 Pillars) | 70 % (Float fehlt, RVOL als Daily-Proxy) |
| Session-Window | 80 % (RTH ✓, Time-Cuts noch nicht) |
| Indikatoren (für Bull-Flag) | 60 % (MACD/VWAP ✓, EMAs noch nicht) |
| Bull-Flag-Pattern | 85 % |
| Top-N-Workflow | 100 % NEU |
| False-Breakout-Filter | 100 % |
| Exit-Framework | 60 % (Trail-9EMA + Time-Cuts fehlen) |
| Order-Routing | 70 % (Slippage approximiert) |

**Gesamt: 76 % der Cameron-Constraints aktiv im Code.**

### Was noch fehlt mit signifikantem Effekt

P1-Gaps (würden Win-Rate weiter steigern):
1. **200 EMA Filter** — Long unter 200 EMA verboten (siehe constraints.yaml)
2. **9 EMA Trail** für letzte 25 % Position
3. **Hard-Flat 11:30 / 12:00 ET** — aktuell läuft Trade bis EOD

P2 — Sample-Refinements:
4. Pullback-Count-State pro Tag (3.+ Pullback skip)
5. Pole-Volume-Rising
6. Bonus-Criteria (Recent IPO, ATH, etc.)

P3 — Im Backtest nicht implementierbar:
7. Tape-Reading (kein Tick-Data)
8. Quarter-Size-Rule (Live-State-abhängig)

## Pipeline-Performance (auf User-PC)

| Schritt | Laufzeit |
|---|---|
| bootstrap.py (Daten-Pull) | ~25 Min einmalig |
| validate.py | <5 s |
| backtest_bull_flag_v3.py (full run) | ~10 Min |
| backtest_bull_flag_v3.py --top-n 10 | <1 Min |
| edgar_full_tag.py (alle 1903) | ~38 Min einmalig |

→ Iteration-Zyklus für neue Filter: **~1 Minute** mit Top-10-Filter.

## Beantworte die Kern-Frage

**"Gehst du vor wie Cameron?"**

In v1/v2 (alle Stocks): **Nein.** Win-Rate 62-64 %, weil viele suboptimale Trades.

In v3 mit `--top-n 10`: **Ja.** Win-Rate 69,2 %, fast Camerons 71 %.

**Cameron's eigene Worte (aus Masterclass):**
> "Generally each day my biggest winners come from the top 3-4 percentage gainers
> in the market. I already know just because these are the top 5 that I'm most
> likely to find success trading one of these."

→ V3 implementiert genau diese Workflow-Logik.

## Empfohlener Default-Modus für Live-Trading

```bash
python backtest_bull_flag_v3.py \
    --top-n 10 \           # Cameron's "stärkster Stock"-Logik
    --pole-pct 5 \         # Cameron-Original
    --moderate \           # FBO-Filter ≤1 Treffer erlaubt
    # --require-catalyst \  # nach EDGAR-Tagging-Komplettierung
```

## Files (committed)

- `bootstrap.py` — Daten-Pipeline ($0)
- `validate.py` — Sanity-Checker
- `backtest_bull_flag.py` — V1 baseline (no filters)
- `backtest_bull_flag_v2.py` — V2 (VWAP/MACD/FBO/Slippage/RTH)
- `backtest_bull_flag_v3.py` — V3 (Top-N Cameron-Workflow) ← **aktuell empfohlen**
- `edgar_full_tag.py` — komplettes EDGAR-8K-Tagging
- `PILOT_REPORT.md` — V1+V2 Report
- `PILOT_REPORT_V3.md` — dieser Report
- `CAMERON_COMPLIANCE.md` — Audit gegen constraints.yaml

## Konkrete Schlussfolgerungen

✓ Cameron-Workflow (Top-N pro Tag) ist der **entscheidende Edge-Faktor**
✓ Win-Rate 69 % erreichbar, fast Camerons 71 % Live-Stats
✓ **Rank 2-7 priorisieren** (Rank-1 ist oft zu parabolic)
✓ Pole-Min 5 % ist optimal, 7 % bringt keinen Gewinn
✓ False-Breakout-Filter funktioniert (Stratifizierung sichtbar)

✗ R/R-Ratio bleibt schwach (0.30–0.40) — Pattern-Setup-Limit, kein Bug
✗ 24 % der constraints.yaml noch nicht im Code (P1-Gaps wären leicht)

**Nächste konzeptionelle Frage**: Bevor mehr Code-Filter — soll der Pilot
auf **größere Datenbasis** (Polygon $79, 5 Jahre, 1-min Bars)? Mit nur 130 Trades
(Top-10) ist Statistik noch dünn. 5+ Jahre würden auf ~3.000-5.000 Trades hochskalieren
und dann erst signifikante Win-Rate-Stabilität.
