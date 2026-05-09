# Setup: Parabolic Momentum / Short Squeeze

Quelle: YouTube `afNhgCc-LCw` (1h33m). Volltext: `notes/transcripts/`.
Camerons Hauptkapitel zu **Stock-Type "intraday parabolic momentum"** —
nicht ein eigenes Pattern, sondern ein Stock-State, in dem Standard-Patterns
(Bull Flag, ABCD, First-Pullback) mit erhöhter Volatilität greifen.

## Definition Parabolic Momentum
- Stock im Squeeze von 100 % – 1.000 %+ am Tag oder über mehrere Tage
- Geht oft mit Halts auf der Up-Side einher (10 %-in-5min-Trigger)
- Volatilität hoch genug für Multi-Dollar-Moves binnen Sekunden
- Trades: gleicher Entry-Trigger wie Standard, aber R/R skaliert hoch

## Typische Auslöser (Stock-Type-Eigenschaften)
- Recent Reverse-Split → Float-Drop, häufiger Squeeze-Kandidat
- Recent IPO → "Initial pop → pullback → squeeze to ATH" Pattern
- SPACs (mergende Special Acquisition Companies)
- Heavy Short Interest + Catalyst
- Sympathy zu einem anderen parabolischen Stock im selben Sektor

## Sympathy-Momentum-Regel (wichtig — neu in den Constraints)
- Wenn 1 Stock parabolisch: 2–4 weitere im selben Sektor squeezen mit
- Diese "Sympathy Stocks" haben oft **gar keine eigene News**
- Beispiel DRYS 2016: GLBS, EC, TOPS, DCIX, SHIP alle 100–1.000 % auf no-news
- Wenn Main-Stock rollt → Sympathy rollt **härter**

## News vs No-News Parabolic
| | mit News | ohne News |
|---|---|---|
| Halt-Risiko (T12) | niedrig | hoch (NYSE mehr als NASDAQ) |
| Boilerplate-Antwort | n/a | "no material developments" |
| Skip-Empfehlung | nein | nicht zwingend, aber Halt-Stop-Risiko bedenken |

## Reverse-Split-Mechanik (Detailerklärung)
```
Pre-Split:  100M Shares × $0.50 = $50M Marketcap
            10:1 Reverse Split
Post-Split: 10M Shares × $5.00  = $50M Marketcap (gleich)

→ Float dropped 100M → 10M
→ Stock fällt jetzt unter Cameron's Float-Cap (<20M)
→ wird tradeable für Cameron
→ Dilution-Cycle: Reverse-Split → Pop → Verkaufen mehr Shares → Reverse-Split → Pop ...
```

## Klassische Parabolic-Verlaufsmuster (Tagesablauf)
1. Pre-Market: News bricht, Stock springt 50–200 % vor 9:30
2. Open: Halt-Up bei 10 %-Move
3. Resume: oft etwas niedriger → Dip-and-Rip → next Halt
4. Stair-Step durch mehrere Halts hoch
5. Eventuell News-Halt durch Exchange (T12) → Resume **deutlich tiefer**
6. Tag schließt mit Reversal oder weiterem Squeeze

## Hedge-Fund-Disaster-Story (BPTH)
- Hedge-Fund-Trader: 800k Short Position
- Stock squeezed ohne News um 200–300 %
- Firm liquidierte seine Short-Position als Schutz
- Loss: $15–20M
- → Don't short parabolic stocks ohne stop-loss-Disziplin

## Halt-Pinning vs Halt-Bricht
- "Pinning": Stock pendelt um LULD-Level (z.B. $54.57), bricht nicht
- "Limp ins Halt": staggers in, oft schwächer beim Resume
- Clean break + Halt: oft Resume **höher**

## Cameron's Risiko-Anpassungen für Parabolic Stocks
- **Spreads** sind das Hauptproblem ($50+ Stocks: $0.75–$1.90 Spread)
- Trade nur Stocks innerhalb seines Wheelhouse ($2–$20)
- Bei extremen Movern wie CAR ($300–$540): nur kleinste Position oder skip
- Mental Stops > Live Stop Orders bei sehr volatile Stocks (Slippage-Risiko)

## Order-Routing (Parabolic-Specific)
- **Kein Market Order** bei Parabolic — viel zu risky (massive Slippage)
- Stattdessen: **Marketable Limit Order** mit Ask+15¢ Offset
- Cameron's Hotkey: Shift+1 = Buy 1.000 @ Ask+15¢, Shift+9 = Buy 9.000
- Sell-Half: Ctrl+L (Ratio-basiert)
- Panic-Exit: Ctrl+Z (Bid–15¢)
- Cancel: Ctrl+anything

## Broker-Empfehlung
- **Lightspeed** > TD Ameritrade für Day-Trading
- Reason: stabile, schnelle Platform, zuverlässige Hotkeys
- TD Ameritrade-Hotkeys "finicky" laut Cameron

## Goldene Regeln aus diesem Video (Mantra)
- "Every day you are either leaving money on the table OR giving back profit"
- "Successful traders take profit; rookies overstay their welcome"
- "What goes up comes back down" (für long-biased Trader: profit takten)
- "The market can stay irrational longer than you can remain solvent"
- "Hesitation = fear → educated intuition for advanced traders, healthy fear for beginners"

## Anti-Patterns (Skip wenn …)
- Parabolic über $20 mit großen Spreads + ohne News (T12-Halt-Risk)
- Sympathy-Trade ohne eigenen Catalyst UND Main-Stock bereits Roll-over
- Late-day Parabolic ohne Volume-Backing
- Kauf bei laufendem Halt-Pinning (kein clean break)
- 4.+ Pullback in einem Multi-Day-Runner
