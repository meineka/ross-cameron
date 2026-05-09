# Ross Cameron Modelling Space

Brain-GIMP zum Analysieren und Nachbauen von Ross Camerons Trading-Strategien
(Warrior Trading). Ziel: aus seinen öffentlichen Setups eine formal beschreibbare,
backtestbare und (perspektivisch) algorithmisch ausführbare Strategie machen.

## Status (Mai 2026 — finalisiert nach Konsistenz-Check)

- **Strategie-Recherche abgeschlossen** für Tier-1-Material:
  - Warrior-Trading-Artikel komplett
  - Buch *How to Day Trade* (Kap. 1–5 als Volltext)
  - 10 YouTube-Strategy-Videos transkribiert + analysiert
  - 1 dreistündige Masterclass (36k Wörter) als Master-Quelle
- **`constraints.yaml`** ist Single Source of Truth, alle Konflikte aufgelöst,
  kanonische Definitionen für `halt_mechanics` und `pullback_count_rule`.
- **10 Setup-Dateien** in `02_setups/` decken alle dokumentierten Cameron-Setups ab.

## Ordnerstruktur

| Ordner | Inhalt |
|---|---|
| `01_strategy_breakdown/` | Überblick, Buch-Notes, Masterclass-Notes, Video-Notes, Quellenliste |
| `02_setups/` | Pro Setup eine .md (10 Stück: Bull Flag, Reversals, Sub-VWAP, …) |
| `03_rules_engine/` | **`constraints.yaml`** = Single Source of Truth, Engine-README |
| `notes/transcripts/` | YouTube-Transkripte als Volltext (10 Stück) |
| `notes/` | offene Fragen, Buch-PDF-Extrakt |
| `04_backtest/` | (noch leer) Backtest-Code |
| `05_data/` | (noch leer) Daten-Quellen |

## Workflow

1. Setup beobachten → in `02_setups/<name>.md` beschreiben.
2. In formale Regeln übersetzen → in `03_rules_engine/constraints.yaml` mergen.
3. Backtest schreiben → `04_backtest/<name>.py` (lädt YAML).
4. Stats prüfen, Edge bestätigen oder verwerfen.
5. Erst wenn Edge da ist: Live-Implementierung (MT5 / Broker).

## Kern-Cameron-Werte (Quick-Reference, Stand finalisiert)

| Bereich | Wert |
|---|---|
| Universum | US Equities, Preis $2–$20 (Sweet Spot $5–$10) |
| Float | < 20M loose, < 10M strict, < 5M = "rocket fuel" |
| RVOL | ≥ 5× (vs 30-Tage-Avg) |
| Tagesbewegung | ≥ +10 % |
| News-Catalyst | Pflicht (Tier-1-Quellen bevorzugt) |
| Time-Window | 07:00–11:00 ET (Power-Hour 09:30–10:30) |
| Indikatoren | 9/20/50/200 EMA, VWAP, MACD 12/26/9, Bollinger 20/2.0, RSI 14 |
| R/R-Ziel | 2:1 minimum (BE bei 33 %), Cameron-Acc-Realität 71 % |
| Max-Loss/Trade | $500 (Standard); Daily-Max = Daily-Goal symmetrisch |
| Position-Sizing | Quarter-Size-Rule + 3k-Block-Scaling (siehe scaling.md) |
| Universal-Trigger | "First green candle to make new high after pullback" |

## Wichtigste Quellen-Hierarchie bei Konflikten

1. **Videos 2024** (aktuellste Cameron-Praxis) — Default
2. Buch 2015 (Kontext + Disziplin-Grundlagen)
3. Warrior-Trading-Webartikel (Aufbereitung)

Konflikt-Tabellen: `01_strategy_breakdown/book_notes.md` und `video_notes.md`.

## Offene Entscheidungen

Siehe `notes/open_questions.md`. **Größte Frage**: Zielmarkt
(US Small Caps original vs MT5-Übertragung) — entscheidet die ganze Daten-Pipeline.
