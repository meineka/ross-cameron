# Setup: Sub-VWAP Trap

Quelle: YouTube `W3jXQlgGbBc` (~25min). Volltext: `notes/transcripts/`.
Live-Beispiel: SCNI, Cameron +$14.279,72 in einer Session.

## Definition
Stock pumpt früh ≥ 10 %, fällt unter VWAP, mehrere gescheiterte Versuche
zurück über VWAP zu kommen → schließlich Break → **Squeeze, oft Halt-Up,
durch HOD**. Shorts gefangen, Longs verpassen es zunächst → Trap.

## VWAP-Berechnung (zur Referenz)
```
VWAP = Σ((H+L+C)/3 × Volume) / Σ(Volume)
```
- Stock > VWAP → Bulls in control
- Stock < VWAP → Bears in control

## Pre-Conditions (alle erforderlich)
- Stock heute bereits **≥ 10 % gepumpt** (Squeeze von Tageshochs gibt es)
- Stock ist **aktuell unter VWAP**
- **Mehrere fehlgeschlagene** Versuche zurück über VWAP (mind. 2)
- Stock baut **Base** (höhere Lows) am unteren Range
- News-Catalyst vorhanden (gibt Sentiment-Reversal-Reason)

## Pattern-Visualisierung
```
            Pop & squeeze (≥10%)
          ╱╲
         ╱  ╲___ Pullback unter VWAP
        ╱    ╲
─VWAP──────────╴╴╴╴╴╴───── ← VWAP-Linie
                     ╲╱╲╱╲╱  ← Base + multiple failed attempts
                          ╱
                         ╱   ← BREAK = Entry-Trigger
                        ╱
                       ╱→ Halt-Up, dann durch HOD
```

## Entry-Trigger
- Base hält (höhere Lows)
- Bid-Side beginnt zu **tightenen** (Spread schmaler)
- **Burst grüner Orders** in Time & Sales
- Erste 1m-Kerze die VWAP **sustained** (close > VWAP, nicht nur Wick)

Cameron-spezifisch:
- Initial-Entry kann **bei Base-Holding** sein (vor VWAP-Break) — Starter
- Voller Add **am VWAP-Break**
- Cameron Live-Beispiel SCNI:
  - Starter $5.85 (1k Shares) am Base
  - Base-Hit-Exit $6.07 (+$220)
  - Re-entry $6.49 für VWAP-Break (10k Shares)
  - VWAP war ~$6.50, Target retest HOD $8
  - Failed mehrfach, eventually worked

## Stop
- Bei Starter (vor VWAP-Break): Low der Base
- Bei VWAP-Break-Add: unter VWAP (= Failure-Confirmation)
- Bei Halt-Up nach Resume: Low der ersten Resume-Kerze

## Targets
1. Retest des HOD (Tageshoch des initialen Squeeze)
2. Halt-Up-Triggering bei +10 %-Move (LULD)
3. Bei Halt-Up + clean Resume: trail unter 9 EMA

## "Defended Levels" (Anti-Pattern-Erkennung)
- Shorts setzen 50k+ Sell-Orders auf Ask **direkt an VWAP**
- Ziel: Buyers abschrecken, "Wall" erzeugen
- Wenn Wall hält → Stock fällt zurück (kein Breakout)
- Wenn Wall bricht → schnelle Acceleration (Shorts cover)
- → Wall = Risiko-Indikator, nicht Skip-Signal

## "20k-Buyer-Ambiguität"
Großer Bid-Stack ist ambig:
- (a) Bullish Whale akkumuliert → Long-Signal
- (b) Bull-Trap (Bait für Longs, dann Dump) → Short-Signal
→ **Confirmation**: hält der Buyer auch wenn Sells durchgehen?
   - Visible Bid bleibt → Iceberg = (a) → bullish
   - Order verschwindet → (b) Bait → bearish

## "Devil Horns"
- Zwei Topping-Tails in Folge auf 1m
- Klassisches Bearish-Rejection-Signal
- → Sub-VWAP-Trap-Trade abbrechen, falls vor Break gesehen

## Liquidity-Distribution-Insight
Order-Book ist **dicht** rund um den aktuellen Preis,
**dünn** in Extension-Zonen.
→ Wenn Stock Range bricht: schneller Move bis zur nächsten dichten Zone
→ Erklärt "Skip"-Verhalten in Parabolic Squeezes

## Wann tritt das Setup auf?
- Oft **Lunch-Time oder Nachmittag** (unerwartete Zeiten)
- Element der Überraschung Teil des Setups
- Day-Trader sind unaufmerksam → schneller Squeeze möglich

## Pros / Cons
| Pros | Cons |
|---|---|
| Big moves wenn es klappt | Inhärente Schwäche (Stock IST < VWAP) |
| Halt-Up sehr wahrscheinlich | Mögliche Ursachen: Company sells shares, Insider dump, Warrant-Exercise |
| Entry klar definiert | Mehrere Tug-of-War-Phasen vor Break — Geduld nötig |
| Nicht-offensichtlich → wenig Crowd | Selten — passt zu wenigen Stocks pro Woche |

## Tools für dieses Setup
- Day Trade Dash (Cameron's eigene Software) — Scanner + Level 2
- VWAP-Plot auf Chart (orange Linie)
- 4 Charts: 10s, 1m, 5m, daily
- Hotkeys (Shift+N für Buy, Ctrl+Z für Panic-Exit)

## Cameron-Hotkeys (verbatim aus dem Video)
| Key | Action |
|---|---|
| Shift+1 | Buy 1.000 @ Ask+15¢ |
| Shift+2 | Buy 2.000 @ Ask+15¢ |
| ... | ... bis Shift+9 = 9.000 Shares |
| Ctrl+Z | Sell full position @ Bid−15¢ (Panic) |
| Ctrl+L / Ctrl+K | Sell into strength @ Ask |
| Ctrl+(any) | Cancel orders |

## Warum funktioniert das Setup?
- Surprise-Element: Sentiment-Shift trifft Shorts unvorbereitet
- Liquidity-Vacuum oberhalb VWAP nach Break
- Halt-Trigger erlaubt keinen "rationalen" Exit für Shorts
- Once Halt-Up: Skip-Behavior in Extension-Zone
