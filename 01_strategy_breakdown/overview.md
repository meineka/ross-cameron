# Ross Cameron — Strategie-Überblick (final, 2026-05-09)

Vollständig konsolidierter Überblick aus 10 YouTube-Transkripten + Buch (Kap.1–5)
+ Warrior-Trading-Artikeln. **Ground-truth** für formale Regeln: `03_rules_engine/constraints.yaml`.

## Trading-Identität
Warrior Trading. Day-Trader. **US Small-Cap Momentum** mit klarem
News-Catalyst, vor allem in den ersten Stunden nach Markt-Open.
$583 → $12M+ verifizierter Gross-Profit über ~10 Jahre.
Aktuelle Win-Rate (Live-Stats): **71 %** auf grünen Tagen, 50 % auf roten Tagen.

## Die 5 Pillars (Stock Selection)
1. **RVOL ≥ 5×** (Tagesvolumen vs. 30-Tage-Avg)
2. **Daily % Change ≥ 10 %**
3. **News Catalyst** (Tier-1-Quellen: Company-PR, Earnings, FDA, Gov, Top-Analyst)
4. **Preis $2 – $20** (Sweet Spot **$5 – $10**, dort 90 % seiner Trades)
5. **Float < 10 Mio Shares** (loose < 20M, "rocket fuel" < 5M)

Alle 5 müssen gleichzeitig zutreffen → AND-Logik.
Premarket-Volumen ≥ 300k (sicher ≥ 1M) als zusätzlicher Liquiditätsfilter.

## Bonus-Criteria (Watchlist-Priority-Boost)
- Recent IPO (≤ 90 Tage)
- Recent Reverse Split (Float-Drop)
- Blue Sky / All-Time-Highs
- Yesterday Volume < 100k

## Die 11 dokumentierten Setups
| # | Setup | Datei | Wichtigster Trigger |
|---|---|---|---|
| 1 | Bull Flag / Micro Pullback | `bull_flag.md` | First green candle makes new high |
| 2 | Gap & Go | (in YAML) | Break of premarket high after open |
| 3 | VWAP Bounce / Hold | (in YAML) | First 1m bullish candle off VWAP + 9EMA-reclaim |
| 4 | Red-to-Green | (in YAML) | 1m close > daily open with volume |
| 5 | Halt Resumption (Long) | (in YAML) | First 1m green candle after resume |
| 6 | ABCD Pattern | (in YAML) | Break of point B on volume |
| 7 | Reversal (Top + Bottom Bounce) | `reversals.md` | 5m candle outside Bollinger + RSI extreme + pin bar + new high |
| 8 | Sub-VWAP-Trap | `sub_vwap_trap.md` | First 1m close > VWAP nach failed reclaims |
| 9 | Parabolic Momentum (Stock-Type) | `parabolic_squeeze.md` | apply standard patterns within parabolic context |
| 10 | Candlestick-Reversal | (in YAML) | 3 long red + Hammer/Bottoming Tail + candle-over-candle |
| 11 | Breaking News | `breaking_news.md` | First pullback after initial pop |

## Universal-Trigger (gemeinsamer Kern)
> "First green candle to make a new high after a 1–3 candle pullback that
> didn't retrace > 50 % and didn't break VWAP — entry on that candle's break,
> stop at the pullback low, target 2:1."

Bull Flag, Gap & Go, VWAP-Bounce, Sub-VWAP-Trap, Breaking News, Halt Resumption
sind alle **Spielarten desselben Patterns** mit jeweils anderem Kontext-Filter davor.

## Charts & Indikatoren (final)
- 4 Time-Frames gleichzeitig: **10s · 1m · 5m · 1d**
- **9 EMA** (gray) — primary trail / dip-buy line
- **20 EMA** (blue) — secondary support
- **50 EMA** (red) — daily trend (nur Daily-Chart)
- **200 EMA** (purple) — main S/R, Trend-Definition
- **VWAP** (orange) — intraday equilibrium
- **MACD** 12/26/9 — front-side-confirmation, locked settings
- **Bollinger** 20/2.0 — nur 5m, nur für Reversals
- **RSI** 14 — nur als Scanner-Filter, nicht auf Chart
- **Volume Bars** — kritisch, separates Pane

## Risk-Framework (kondensiert)
- **R/R minimum**: 2:1 (BE bei 33 % Win-Rate)
- **Accuracy-Targets**: min 50 %, optimal 65 %, stretch 70 %
- **Max-Loss/Trade**: $500 (Standard, skaliert mit Konto)
- **Daily-Max-Loss = Daily-Goal** (symmetrisch)
- **50 %-Drawdown-Rule**: 50 % der Tagesgewinne zurückgegeben → STOP
- **Red-Streak**: 2 rote Tage → max-daily auf 50 %; 4 → auf 25 %
- **Quarter-Size-Rule**: ¼ Position bis +20 ¢/Share kumuliert
- **3k-Block-Scaling**: bei 4 Continue-Signalen Adds alle 10 ¢
- **Add-to-Losers**: VERBOTEN
- **Long unter 200 EMA**: VERBOTEN (rare exception)

## Time-Window
- **Cameron's Active Window**: 07:00–11:00 ET (90 % der Trades)
- **Power-Hour-Edge**: 09:30–10:30 (höchste Win-Rate)
- **Hard-Stop**: 11:30 ET für neue Entries; 12:00 hard flat
- **Avoid**: nach 13:00 ET (selbst Cameron landet hier meist in Verlust)
- **30-Min-Timeout**: 30 Min ohne Setup → Session-Ende

## Order-Mechanik (Extended Hours)
- Pre-Market (07:00–09:30): Limit + Offset (+15¢ buy / −15¢ sell)
- After-Hours (16:00–20:00): **nicht profitabel** für Cameron → skip
- **Forbidden**: Market Orders, Stop Orders, Options
- **Hotkey-Schema**: Shift+1..9 = buy 1k–9k @ Ask+15¢

## False-Breakout-Filter (5-Indikator-Checkliste)
≥ 2 Treffer = SKIP:
1. MACD against trade
2. Volume profile rot-heavy
3. History of false breakouts heute
4. Multiple topping tails
5. Too long consolidating

## Was diskretionär bleibt
- Tape-Reading (Level 2 + Time & Sales) — durch Algo schwer 1:1 zu kopieren
- "Stärkster Stock des Tages" — durch Scanner-Score approximierbar
- Hidden-Buyer/Whale-Detection — Heuristiken in `level2_reading.md`
- Marktphase Hot/Medium/Cold — beeinflusst Aggressivität

## Was als nächstes konkret zu tun ist
1. **Markt-Entscheidung**: US Small Caps original vs MT5-Übertragung
2. **Daten-Pipeline** für 1m-Bars + Premarket + Float + News
3. **Backtest-Skelett** das `constraints.yaml` lädt
4. Erstes Setup: **Bull Flag / Micro Pullback** als sauberster Start

→ Siehe `notes/open_questions.md` für Detail-Fragen.
