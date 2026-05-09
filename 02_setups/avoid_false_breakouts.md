# Avoiding False Breakouts (Bull-Trap-Filter)

Quelle: YouTube `1FKu4LH0Xss`. Volltext: `notes/transcripts/`.
**Das wichtigste Filter-Video überhaupt** — gibt eine konkrete 5-Indikator-Checkliste
zur Erkennung wahrscheinlicher False Breakouts.

## Definition
**False Breakout / Bull Trap**: Stock bricht über Resistance, sieht aus wie Continuation,
dann sofortige Reversal mit großem Volume → Kerze hat langen Topping Tail oder Jackknife.
Trader die kaufen, sind sofort tief im Minus (oft −1$/Aktie in Sekunden).

## 5-Indikator-Checkliste (Cameron's Hauptfilter)
**Wenn ≥ 2 Indikatoren zutreffen → wahrscheinlich False Breakout, NICHT traden:**

| # | Indikator | Was prüfen |
|---|---|---|
| 1 | **MACD against trade** | MACD < Signal-Line auf 1m beim Entry-Versuch |
| 2 | **Volume Profile rotgewichtet** | rote Kerzen haben höheres Volumen als grüne |
| 3 | **History of False Breakouts** | Stock hat heute schon 1+ Rejection-Candles produziert |
| 4 | **Multiple Topping Tails** | mehrere Kerzen mit Topping-Tail in Folge ODER an gleicher Resistance |
| 5 | **Too Long Consolidating** | Range-bound > 5–10 Kerzen ohne klare Direktion |

## Front-Side vs. Back-Side eines Moves

| Front-Side | Back-Side |
|---|---|
| MACD divergent (lines spreading) | MACD crossed-down (lines converging) |
| Alle Kerzen grün, lighter red Pullbacks | Choppy, sideways |
| Volume profile clean | Volume profile gemischt/rot-heavy |
| Tradeable | **Skip — wait for new setup** |

→ Kerze nach **MACD-Cross-Down**: "End of front side" — kein neuer Long-Trade
→ Front-Side ist **Premarket + erste 30–60 Min nach Open** typischerweise

## Mechanik: Algo-Spike + Algo-Flush

```
Phase 1: Range-bound consolidation
   → MMs sit on bid AND ask, profit from spread

Phase 2: Buy orders surge through
   → MMs PULL their sell orders (would otherwise be net short unlimited)
   → Order-Book oben löscht sich aus
   → Stock SKIPS Levels nach oben (z.B. von $7 direkt $7.30 ohne Fills dazwischen)

Phase 3: At top, sellers come in
   → Profit-takers + Insiders dumping shares
   → MMs PULL their buy orders (would otherwise be net long unlimited)
   → Order-Book unten löscht sich aus
   → Stock SKIPS Levels nach unten = "Jackknife Candle"
   → Trader die im Pop kauften: instant -$1/Aktie
```

→ Daher die typische Form: kleiner Wick nach oben (algo-spike), riesiger Wick nach unten
   (algo-flush). Kann in einer einzigen 1m-Kerze passieren.

## Warum Stocks nach langer Konsolidierung brechen
- **Insider dumping**: jemand der lange hält, sieht 100%-up als Verkaufschance
- **Direct Offering**: Company verkauft Shares; Announcement kommt oft ERST später
- **Whale at Resistance**: 500k Sell-Order sitzt am Level, mehrere Tap-Versuche
- **Whale Number 2**: nach erstem Whale-Sweep kommt zweiter, noch größerer Seller

## Whale-at-Resistance-Pattern (häufigste Fall)
```
1. Stock pumpt zu Level X (z.B. $7)
2. Whale sitzt mit 500k Sell-Order am Level
3. Trader kaufen: 1. Tap → Reject, Pull-back
4.                2. Tap → Reject, Pull-back
5.                3. Tap → Reject (sieht aus wie "wall")
6.                4. Tap → bricht endlich!
7. ABER: Whale 2 wartet bei $7.05 mit 1M Sell-Order
8. Stock hits $7.10 → Whale-2 dumps → Jackknife zurück zu $6.00
```

→ "1 2 3 4 5 Versuche an gleichem Level" = klassisches Whale-Setup → KEINE Trade-Empfehlung

## Pre-Market = Cleanste Phase
- Holders sind noch nicht aktiv (Alerts triggern erst um 9:30/10:00)
- Insider sehen ihren Bestand nicht in Echtzeit pre-market
- Direct-Offerings selten pre-market angekündigt
- → Beginning-of-Move ist meist **am sauberste**, daher Cameron's 7–11 ET Fokus

## Konkrete Trade-Decisions (verbatim Cameron)
- 1. Pullback: aggressive Entry OK
- 2. Pullback: aggressive Entry OK
- 3. Pullback: nur wenn MACD positiv UND Vol-Profile positiv UND keine False-Breakouts heute
- 4.+ Pullback: **SKIP**
- 5min-Pullback nach Parabolic: oft "zu extended", warten auf ABCD/Bull-Flag-Confirmation

## Marketmaker-Mathematik (Detail)
- Spread × Volume = Profit (5¢ Spread × 100k = $5.000)
- Goal: equal buys/sells täglich (delta-neutral)
- Bei Volatility: Orders weg, weil unlimitierte Net-Position-Risk

## Verzweiflungs-Modus-Erkennung (für sich selbst)
Wenn man bei einem False Breakout caught wird:
- "I can't believe it"
- "How does this keep happening to me"
- "Another false breakout I'm so sick of this"
→ Das sind die Spiral-Trigger → STOP, Pause, kein weiterer Trade heute
