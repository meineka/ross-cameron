# Konsistenz-Audit (Stand 2026-05-09, finalisiert)

Dokumentiert die Konsolidierungs-Schritte am Master-File `constraints.yaml`
nach Abschluss der Tier-1-Recherche.

## Behobene strukturelle YAML-Bugs

| # | Bug | Behebung |
|---|---|---|
| 1 | `breaking_news` war fälschlich unter `order_routing` eingerückt | Verschoben in neue Sektion `entries_extended` (4.11) |
| 2 | `candlestick_reversal` war fälschlich unter `extended_hours_mechanics` eingerückt | Verschoben in `entries_extended` (4.10) |
| 3 | Doppelter Sektions-Tag `4a)` (einmal Order-Routing, einmal Level-2) | Level-2 zu `4f)` umbenannt |
| 4 | YAML-Key `leverage_after_16:00` (Doppelpunkt = parser ambiguity) | In String gequotet |
| 5 | YAML-Key `t2: halt_up_trigger_at_+10_pct` (Plus-Zeichen) | In String gequotet |

## Aufgelöste Doppel-Definitionen (kanonische Single-Sources)

### `halt_mechanics` (neu, kanonisch)
Vorher dreifach definiert mit leichten Abweichungen:
- `level2_rules.circuit_breaker_halt`: 10 %, 15 s
- `breaking_news.luld_halt_thresholds`: 10 % (L1) und 20 % (L2)
- `parabolic_momentum.halt_mechanics`: pinning, limp_in, T12-Risk

→ Konsolidiert in **eine** kanonische Definition `halt_mechanics`.
   Alle Stellen referenzieren via `halt_reference: "siehe halt_mechanics"`.

Inkludiert: Level-1 (10 % in 5 min), Level-2 (20 % über 5-min-Avg),
15 s Bid-Hold, Resume-Pattern, T12-Risk-Verteilung NYSE > NASDAQ.

### `pullback_count_rule` (neu, kanonisch)
Vorher widersprüchlich an drei Stellen:
- `bull_flag.pullback_count_limit: 2` (= 3. nicht traden)
- `ma_trading_rules.pullback_priority`: 1./2. aggressive, 3. skip
- `false_breakout_filter.pullback_count_rule`: 1./2. ok, 3. nur conditional, 4+ skip

→ Konsolidiert: konservative Variante gewinnt.
   3. Pullback nur wenn ALLE conditional_allow-Bedingungen erfüllt
   (MACD positiv + Vol-Profile grün + keine False-Breakouts heute + nicht > 2 ATR off 9EMA).
   4+ immer skip.

Quellen: Buch Ch.5 + 1FKu4LH0Xss (Cameron's strikteste Variante).

## Bekannte Konflikte zwischen Quellen (im YAML als strict-Wert dokumentiert)

| Größe | Buch 2015 | Videos 2024 | YAML-Wert | Begründung |
|---|---|---|---|---|
| Float Maximum | 50M (loose <10M) | 20M (strict <10M) | strict 10M, loose 20M | Videos = aktuellste Praxis |
| RVOL Minimum | 2× ("interesting") | 5× (Five Pillars) | 5.0 | Selektivität gestiegen |
| Pullback-Regel | 1./2. agg, 3. cautious | 3. nur conditional | konservativ | Risk-Management-Priorität |
| Tagesfenster | unbestimmt | 07:00-11:00 ET | 07:00-11:00 | Konkretisiert in Videos |
| Preis-Untergrenze | $1 | $2 | strict $2, loose $1 | Cameron-Default-Wert |
| Sweet Spot Preis | nicht spezifiziert | $5-$10 | $5-$10 | Aus Profit-Distribution-Stats |

## Validierung der finalen YAML-Struktur

**Top-Level-Sektionen** (in Lese-Reihenfolge):
1. `universe` — 5 Pillars, Bonus-Criteria, Filings, Profit-Distribution
2. `session` — Time-Window
3. `charts` + `indicators` — Chart-Setup
4. `ma_trading_rules` — MA-spezifische Regeln
5. `pullback_count_rule` (NEU, kanonisch)
6. `entries` — 9 Entry-Models (4.1–4.9)
7. `order_routing` — Hotkey-Schema, Broker
8. `entries_extended` — 2 weitere Entry-Models (4.10–4.11)
9. `halt_mechanics` (NEU, kanonisch)
10. `level2_rules` — Tape-Reading
11. `extended_hours_mechanics` — Pre/After-Hours
12. `false_breakout_filter` — 5-Indikator-Checkliste
13. `bollinger_bands_detail` — Reversal-Workflow
14. `exits` — Scale-out, Hard-Exit-Signals, Scalp-Exits
15. `risk` — R/R, Position-Sizing, Scaling, Tracking, Discipline
16. `psychological_levels`
17. `vetos` — 27 Hard-Skips
18. `beginner_roadmap`
19. `discretionary_notes`

## Unvermeidbare verbleibende Redundanzen

- **"Big Seller 100k+ on Ask"**: einmal in `level2_rules.big_seller_threshold_shares`
  und einmal in `exits.hard_exit_signals` als Veto.
  → Bewusst beibehalten, weil unterschiedliche Konsumenten (Tape-Reader vs Exit-Engine).

- **VWAP** wird mehrfach als Stop/Target referenziert in einzelnen Setups —
  jedes Setup hat seine eigene VWAP-Logik (z.B. Sub-VWAP-Trap nutzt VWAP als
  Trigger-Linie, Bull Flag als Untergrenze). Beibehalten.

## Nicht-aufgelöste offene Fragen

Siehe `notes/open_questions.md`. Wichtigste:
- **Zielmarkt**: US Small Caps original vs MT5-Übertragung
  → Entscheidet, welche Cameron-Constraints überhaupt anwendbar sind
  (z.B. Float-Filter ist auf MT5 sinnlos, Reverse-Split-Bonus auch).
