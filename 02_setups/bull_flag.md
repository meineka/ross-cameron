# Setup: Bull Flag / Micro Pullback

> **Hinweis**: Diese Datei ist ein erster Entwurf vom Mai 2026 mit älteren
> Schwellen (5%-Pole, 30-40% Range etc.). Die **kanonische Spezifikation**
> steht in `03_rules_engine/constraints.yaml#entries.bull_flag_micro_pullback`
> und im Cameron-Wording in `01_strategy_breakdown/masterclass_notes.md` und
> `02_setups/avoid_false_breakouts.md`.
>
> Werte hier sind nur als visueller Schnellüberblick gedacht und können
> leicht von den YAML-Werten abweichen. Bei Konflikt **YAML gewinnt**.

## Idee
Starker Impuls-Move (Pole) → enge, leicht abwärts/seitwärts gerichtete
Konsolidierung (Flag) → Ausbruch über Flag-High = Long-Entry.

Auf 1m-Chart: "Micro Pullback" — 1–3 rote Kerzen.
Auf 5m-Chart: "Bull Flag" — klassischere Form mit längerer Konsolidierung.
Es ist **das gleiche Pattern**, nur anderer Time-Frame.

## Visuelle Kriterien (kurz)
- **Pole**: 3–7 grüne Kerzen, kumulativ ≥ 5 % (YAML strict).
- **Flag**: 1–3 rote Kerzen, Retracement ≤ 50 % (optimal ≤ 25 %).
- Volumen in Flag fällt; Volumen im Breakout steigt.
- **Kein Topping Tail** am Pole (oberer Docht > 40 % Range = Schwäche).
- Flag muss **über VWAP halten**.

## Entry-Trigger
- Erste grüne Kerze deren High > High der vorherigen roten Kerze
- Bestätigung: Burst grüner Orders in T&S
- Volumen-Faktor: ≥ 1.5× SMA(20) of Volume

## Stop / Target
- **Stop**: min(flag_lows)
- **T1**: Entry + 1R (50 % raus, Stop auf BE)
- **T2**: nächstes psych. Level (whole/half dollar)
- **T3**: trail unter 9 EMA bis Bruch

## Bekannte Failure Modes
- Falscher Ausbruch ohne Volumen → wird oft sofort verkauft.
- Pole bereits zu lang gelaufen (extended) → schlechtes R/R.
- Marktkontext bearish / SPY rot → Setup-Qualität sinkt deutlich.
- 4+ rote Pullback-Kerzen → SKIP (Schwäche-Indikator).
- 3.+ Pullback in Folge → siehe `pullback_count_rule` in YAML.
