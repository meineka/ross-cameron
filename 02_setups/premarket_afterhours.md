# Premarket & After-Hours Trading Mechanics

Quelle: YouTube `PjVivCcM1B0`. Volltext: `notes/transcripts/`.
Kein eigener Setup-Pattern — sondern die **Order- und Markt-Mechanik** für Trades
außerhalb der regulären Marktzeiten. Zentral wichtig für Cameron's heutigen Workflow,
weil 90% seiner besten Trades um den Open herum oder im Premarket entstehen.

## Marktzeiten (Eastern Time / NYC)
| Phase | Zeitfenster | Camerons Nutzung |
|---|---|---|
| Pre-Market | 04:00 – 09:30 | aktiv ab 06:30 (Volumen ab 07:00) |
| Regular | 09:30 – 16:00 | primärer Power-Hour-Slot |
| After-Hours | 16:00 – 20:00 | nicht profitabel (eigene Daten!) |
| 24h | nur ausgewählte Stocks/Broker | n/a |

**Most-Broker-Realität**: nicht 04:00, sondern erst **07:00** Pre-Market-Trading erlaubt.
**Volumen-Surge** typisch um 07:00 ET (News-Releases starten konzentriert da).

## Hard-Order-Rules in Extended Hours (Pre + After)
- **NO Market Orders** — werden automatisch rejected
- **NO Stop Orders** (kein Buy-Stop, kein Sell-Stop)
- **NO Options Trading** in Extended Hours
- **NO Leverage after 16:00** — Margin-Positionen müssen bis 15:45 geschlossen sein,
  sonst Auto-Liquidation um 16:00
- **Leverage erlaubt im Pre-Market** (Cameron-spezifische Beobachtung)
- **GTC-Orders sitzen passive** — triggern erst beim 09:30 Open

→ **Einzige zulässige Order-Form**: **Limit Order mit Offset**
   - Buy: Limit @ Ask + 10–15¢
   - Sell: Limit @ Bid − 10–15¢

## Warum keine Stop-Orders erlaubt sind
- Verhindert "Stop Hunting" durch Hedge Funds
- In light-volume Sessions könnte ein großer Order eine Flash-Crash-Kaskade lösen aus
- Stops würden in Reihe getriggert → Whale kauft am Boden zurück
- Technisch illegal, aber MMs haben keine Fiduciary-Pflicht zu Retail
- → Exchange schützt Markt durch Stop-Verbot Premarket

## HFT-Algos: "Light Switch"
- 09:30: Order-Book füllt sich schlagartig (Light an)
- 16:00: Order-Book leert sich (Light aus)
- Pre/After-Hours: HFT-Algos sind weitestgehend **abgeschaltet**
- → Kleinere Positionen können größere Moves auslösen
- → Stocks moven cleaner in Extended Hours (weniger Mid-Trader-Konkurrenz)
- → ABER auch: weniger Liquidität, größere Spreads

## Cameron's Performance-Daten (verbatim aus Video)
- 2016–2019: $111k Premarket-Profit über 4 Jahre (= sehr wenig)
- 2020+: nach März 2020 Premarket-Profit explodiert
- Today: Trades konzentriert 07:00–11:00 ET
- After-Hours: **nicht net-profitabel** (Cameron tradet das nicht)

→ Für unsere Modellierung: After-Hours-Trades als Veto, Premarket ab 07:00 ist Edge-Zone.

## Broker-Specifics (zur YAML-Constraint-Vollständigkeit)
| Broker | Setup für Pre/After-Hours |
|---|---|
| **Lightspeed** | Limit + TIF=Day arbeitet 04:00–20:00 automatisch |
| **TD Ameritrade / thinkorswim** | TIF muss auf "Extended" gesetzt werden, "Day" wird rejected |
| **Webull** | Turbo Trader Settings: offset orders + percentage exits konfigurierbar |
| Hotkey-Routing | siehe `constraints.yaml#order_routing` |

## Why Most Days Begin in Pre-Market (Camerons Logik)
1. News released zu festen Slots (volle/halbe Stunde, oft 07:00, 08:00, 08:30)
2. HFT-Algos parsen Headlines via Keywords → instant buy
3. Retail-Trader können einsteigen, wenn Pattern visible (= micro pullback nach erstem Pop)
4. Stock kann 200–300% laufen **vor** 09:30
5. Wer auf 09:30 wartet → verpasst 80% des Moves

## Anti-Patterns in Pre-/After-Hours
- Order ohne Offset → fillt nicht, sitzt dann passive
- Marktorder versenden → automatic Rejection
- Margin-Position nach 15:45 halten → Auto-Liquidation Risk
- Stop-Order versenden → wird gar nicht akzeptiert
- Trading in After-Hours mit Erwartung Premarket-Volumen → Liquidity-Falle
- Vergessen, dass Daily-Charts oft NUR Regular-Hours-Candles zeigen → Charts irreführend
