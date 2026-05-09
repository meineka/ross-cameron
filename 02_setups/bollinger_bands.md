# Bollinger Bands — Detail-Anwendung

Quelle: YouTube `DgWn3egDGb0`. Volltext: `notes/transcripts/`.
Vertieft das Buch-Kapitel 5 (Bollinger 20/2.0) mit konkretem Reversal-Workflow.

## Settings (NICHT ändern!)
- **Period**: 20
- **Standard Deviations**: 2.0
- **Source**: Close
- → "Self-fulfilling prophecy": andere Trader sehen das gleiche Signal mit gleichen Settings.
   Custom-Settings = isoliertes Signal = kein Edge.

## Was Bollinger Bands kommunizieren
- **95% des Price Action** liegt **innerhalb** der Bands
- Kerze **vollständig außerhalb** = **Extreme Situation** = High-Reversal-Probability
- "Rubber Band stretched out" — je weiter draußen, desto höher Snap-back-Likelihood

## Formal: Reversal-Setup mit Bollinger Bands
**Long Bottom-Bounce:**
```
Pre-Conditions (alle):
- 5–10 aufeinanderfolgende rote Kerzen
- Finale Kerze VOLLSTÄNDIG unter unterem Band
- RSI < 10 (oder < 5 ultra-extreme)
- Pin Bar / Bottoming Tail / Doji als finale Kerze
- Volume peak auf finaler roter Kerze

Entry-Trigger:
- NÄCHSTE Kerze macht ein neues HIGH (over previous red high)
- "Candle-over-candle confirmation"

Stop:
- Low of Day (LOD)

Targets (in Reihenfolge):
- T1: 9 EMA (erste Resistance)
- T2: 20 EMA
- T3: VWAP ("rubber band slack point" / equilibrium)
- T4: midline of Bollinger Bands
```

**Short Top-Reversal**: spiegelverkehrt
- Finale Kerze über oberem Band, RSI > 90, Topping Tail / Shooting Star
- Trigger: nächste Kerze macht new low
- Stop: High of Day (HOD)
- Targets: 9 EMA → 20 EMA → VWAP

## "Outside-Band" ist Vorwarnung, NICHT Auslöser
Verbatim: "It's a high likelihood that we will reverse, but it is not a guarantee."
- Erst Confirmation-Candle (next-makes-new-high/low) löst Trade aus
- Stocks können **lange außerhalb** bleiben in Parabolics → nicht zu früh shorten
- Cameron-Anchor: "irrational longer than you can remain solvent"

## Bollinger-Band-Compression als Setup-Signal
- BBs kommen **nah aneinander** → Range schmilzt
- Konsolidierungsphase
- Followt typischerweise eine **Expansion** = Breakout (auf-/abwärts)
- Direction-Hinweis: Position relativ zu VWAP
  - Über VWAP + Compression → wahrscheinlich Breakout up
  - Unter VWAP + Compression → wahrscheinlich Break down
- → Compression ist **Vorlauf-Indikator** für Volatility-Expansion

## Doji + Outside-BB = Verstärkter Reversal-Signal
- Doji allein = Indecision
- Doji im Bollinger-Extreme-Zone = **amplified** Reversal-Signal
- Beispiel SMCI: Doji am Tagestief weit unter unterem Band → Snap-back-Trade

## Wann Cameron Bollinger Bands NICHT nutzt
- Auf 1m-Chart: nur wenn er aktiv Reversal tradet
- Nicht für Momentum-Long-Trades (Front-Side)
- Nicht für Trend-Following (zu lagged)
- Nur auf 5m primär (Buch-Konsistenz)

## Visualisierung der Bollinger-Targets bei Reversal
```
        ┌─────── Upper Band
        │      ↑ Top-Reversal Entry hier
        │  ┌── 9 EMA ── T1 für Bottom-Bounce
        │  │  
   ─────┼──┴── 20 EMA ── T2
        │   
   VWAP ┼────── Mid-Line ── T3 / "Rubber Band Slack"
        │  
        │  ┌── 20 EMA
        │  │
        │  └── 9 EMA ── T1 für Top-Reversal
        │      ↓ Bottom-Bounce Entry hier
        └─────── Lower Band
```

## Camerons Live-Stats-Anchor (aus diesem Video)
- Lifetime: $12M+ Gross Profit
- Win Rate: ~68% (= Loss Rate ~32%)
- → Auch Cameron ist 32% der Zeit falsch
- → Edge kommt aus 2:1 R/R, nicht aus Akkuratesse

## Camerons "Essential Indicators" (verbatim Liste)
1. **Bollinger Bands** (für Reversals)
2. **MACD** (für Front-Side-Confirmation)
3. **RSI** (für Extremes-Filter, im Scanner)
4. **EMAs / SMAs** (Trend & Trail)
5. **VWAP** (Equilibrium)

→ "Don't add 25 indicators — analysis paralysis"

## Anti-Patterns
- Trade Reversal ohne Confirmation-Candle (= Anticipation)
- Custom-Settings (z.B. Period 37 Stddev 3.5) → isoliertes Signal
- Bollinger auf Daily-Chart für Day-Trading (Scope-Mismatch)
- Bollinger als alleiniger Indikator (immer mit MACD + Vol-Profile cross-check)
- Kerze außerhalb Band → sofort einsteigen ohne RSI-Confirmation
