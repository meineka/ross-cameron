---
name: Ross Cameron Modelling Project
description: Workspace at ~/ross-cameron/ for analyzing and rebuilding Ross Cameron's day-trading strategies into mechanical, backtestable rules
type: project
originSessionId: 15f3b556-c7bf-427c-a767-d5b482db6533
---
Workspace: `C:\Users\Szymon\ross-cameron\` — getrennt vom algo-miner Projekt.

Ziel: Ross Camerons Setups (Warrior Trading, US Small-Cap Momentum) in formale,
backtestbare Regeln übersetzen und perspektivisch algorithmisch ausführbar machen.

Struktur:
- 01_strategy_breakdown/overview.md — recherche-konsolidierter Überblick
- 01_strategy_breakdown/sources.md — alle Primär-/Sekundärquellen, Konflikte dokumentiert
- 02_setups/bull_flag.md — erstes Setup mit Regelentwurf
- 03_rules_engine/constraints.yaml — **Single Source of Truth** mit allen harten Regeln
  (5 Pillars Universe, Session, Indikatoren, 7 Entry-Modelle, Exits, Risk, Vetos)
- 03_rules_engine/README.md — wie die Engine die YAML konsumieren soll
- 04_backtest/ — leer, nächster Schritt
- 05_data/ — leer, abhängig von Markt-Entscheidung
- notes/open_questions.md — offene Fragen, v.a. Zielmarkt (US Stocks vs. MT5)

Recherche-Stand 2026-05-09: Web-Artikel + 7 YouTube-Transkripte (pickscribe.com)
sind ausgewertet und konsolidiert. constraints.yaml enthält jetzt zusätzlich:
- Preis-strict $2-$20 (Sweet Spot $5-$10), Time-Window 07:00-11:00 ET
- 5-7-Kerzen Bull-Flag-Spec, "3. Pullback nicht mehr traden"
- Big-Seller-Exit (100k+ auf Ask), Jackknife-Exit, 200EMA-Bruch-Exit
- Position-Sizing-Schema: ¼-Start, ¼-daily-goal-Schwelle, Doppeln auf Winnern
- Daily-Max = Daily-Goal symmetrisch, 50%-Drawdown-Rule
- Accuracy-Targets 50/65/70%, BE bei 33%
- Cameron Live-Stats (51-Tage-Challenge) als Benchmark
- Beginner-Roadmap (90d Sim → 10 Shares → 160 Shares)
- Psychological Levels (whole/half dollars)
Pickscribe-Mirror funktioniert für YT-Transkripte; ytscribe.com redirectet dorthin.

**Buch-Auszug eingearbeitet** (5CH_How_To_Day_Trade_Ross_Cameron PDF, Kap. 1–5):
- Volltext extrahiert nach `notes/cameron_book_chapter5.txt` via pdftotext
- Strukturiert in `book_notes.md` (Risk, Stock Selection, Candlesticks, Indikatoren)
- Neue YAML-Constraints: Former-Runners, Inside-Day-Skip, Follow-Through bevorzugt,
  Buyouts Skip, Long<200EMA Forbidden, Add-to-Losers Forbidden, Risk-Balancing,
  Trade-Tracking-Pflicht, 30/15-min Sport/Meditation-Routine, Reversal-Setup
  (Bollinger 20/2.0 nur 5m, RSI 20/80 als Scanner-Filter, Candlestick-Reversal)
- Konflikte Buch (2015) vs. Videos (2024) dokumentiert: Float 50M→20M, RVOL 2x→5x,
  Pullback-Regel "3. vorsichtig" → "3. nicht traden". Strategie wurde strikter.

PDF-Tool: pdftotext (mingw64) ist verfügbar — funktioniert für weitere PDFs.
Buch-Kapitel 6–11 fehlen im Auszug (nur 5-Kapitel-Sample).

**Masterclass-Transkript eingearbeitet** (B81TMhUpz50, "27 Years of Trading Knowledge"):
- Volltext lokal: `notes/transcripts/B81TMhUpz50_27years_masterclass.txt` (36k Wörter)
- Strukturiert: `01_strategy_breakdown/masterclass_notes.md`
- Workflow: Chrome MCP → pickscribe.com/v/<ID> → click "TXT herunterladen" → Datei in ~/Downloads
- Pickscribe paywall'd jetzt nicht-cached Videos. Free funktioniert nur für Top-Cached
  (Masterclass mit 374k Views ging). Andere geben "Vollständiges Transkript entsperren"
  Lorem-Ipsum-Placeholder, body=1982 chars vs. unlocked body=187k chars.
- Alternative: Login bei pickscribe gibt 50 free/Monat — wäre nächster Schritt für mehr.

**Aus weiteren Tier-1-Videos eingearbeitet** (User pastet manuell ins Chat):
- KzVbXzkoZkA Scaling: 3k-Block, Hot/Cold-Market-Anpassung, Sell-Half-Hotkey
- afNhgCc-LCw Parabolic Momentum: Sympathy-Momentum-Regel, T12-Halt-Risk auf NYSE,
  vollständiges Hotkey-Schema, Lightspeed-Empfehlung, Reverse-Split-Mechanik
- W3jXQlgGbBc Sub-VWAP-Trap: komplettes Setup, "Defended Levels", "Devil Horns",
  Live-Beispiel SCNI
- PjVivCcM1B0 Pre-/After-Hours: Extended-Hours-Order-Rules, Broker-Setup,
  HFT-Light-Switch, After-Hours nicht profitabel für Cameron
- JMcaRfFThmg Breaking News: "wait for response not search news", 7-8c Stop,
  Float-Exception-Regel, NBEV-Live-Trade
- 1FKu4LH0Xss False Breakouts: **5-Indikator-Checkliste** (wichtigster Defensiv-Filter),
  Algo-Spike+Flush-Mechanik, Whale-at-Resistance-Pattern,
  Pullback-Count-Rule (1./2. OK, 3. nur mit Bedingungen, 4.+ skip)
- DgWn3egDGb0 Bollinger Bands: 20/2.0 locked, Compression→Expansion-Pattern,
  4-Target-Sequenz (9EMA→20EMA→VWAP→Midline), Doji+Outside-Band amplification

ALLE 10 Tier-1-Videos der Strategy-Playlist sind jetzt im Brain integriert.
Insgesamt: Masterclass + 9 weitere Setup-spezifische Transkripte → vollständiges
Cameron-System dokumentiert.

**Konsistenz-Check abgeschlossen (2026-05-09 finalisiert)**:
- `constraints.yaml` parst sauber als YAML (20 Top-Level-Sektionen, validated mit pyyaml)
- Strukturelle Bugs behoben: mis-indented `breaking_news` und `candlestick_reversal`,
  duplicate "4a" Sektions-Tag, unquotierte Special-Char-Keys (`leverage_after_16:00`,
  `+10_pct`)
- Doppel-Definitionen aufgelöst durch kanonische Single-Sources:
  * `halt_mechanics` (vorher dreifach in level2_rules/breaking_news/parabolic_momentum)
  * `pullback_count_rule` (vorher widersprüchlich in bull_flag/ma_trading_rules/false_breakout_filter)
- Cross-References via `halt_reference:` und `pullback_count_reference:` Schlüssel
- Bull-Flag-MD als "Quick-Überblick" markiert, YAML als Ground-Truth
- `CONSISTENCY.md` dokumentiert alle Konsolidierungs-Schritte
- README + overview.md spiegeln finalisierten Stand

Stand-Zähler: 9 Entry-Models + 2 Extended-Entries, 28 Vetos, 5-Indikator-False-Breakout-
Filter, kanonische Halt-Mechanik und Pullback-Count-Rule.

**Aus Masterclass neue Constraints in YAML**:
- Quarter-Size-Rule (¼ Position bis +20¢/Share kumuliert, dann Full-Size)
  → Camerons "45 grüne Tage Jul-Sep 2024"-Discovery
- Bonus-Criteria: Recent IPO, Recent Reverse-Split, Blue-Sky/ATH, Yesterday-Vol<100k
- Catalyst-Timing: Mon/Tue/Wed > Friday-PM
- Tier-1-News-Sources Liste
- Profit-Distribution-Anchor: 99% Profit aus >10%-Movern, <4% aus <$2-Stocks
- Top 3-4 Percent-Gainers = primary winner pool
- A-Quality-Setup-Density pro Markt-Phase (hot/medium/cold)
- Spiral-Mechanik 5-Stufen + Intervention
- Live-Stats Winning vs Losing Day (50% Acc an Loser-Tagen!)
- SEC-Filings-Liste (10Q/K, 13D/G, Form-4, S-1/S-3)
- Share-Size-Progression 50→20k bis 50k "cool as cucumber"
- Sweet Spot Preis $5-$10 (innerhalb $2-$20)

**Why:** Szymon will Cameron-Stil systematisch nachbauen, nicht nur diskretionär nachhandeln.
**How to apply:** Wenn er an Cameron-Strategien weiterarbeitet, in diesem Ordner bleiben,
nicht im algo-miner Projekt vermischen. Erstes konkretes Setup in Arbeit: Bull Flag.

Offene Kern-Entscheidung: Zielmarkt — echte US Small Caps (originalgetreu) oder
Übertragung auf MT5-Instrumente. Steht noch aus.
