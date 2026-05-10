# Quality Gates — Cameron-Bot

Standardtests die nach **jeder** Code-Änderung laufen müssen.

## Running

```bash
# Full Suite (3 Min mit Replay-Tests)
python -m pytest tests/ -v --tb=short

# Schnelle Subset (ohne Replay/Scanner)
python -m pytest tests/ -v -k "not replay and not scan_only"

# Single test
python -m pytest tests/test_pattern_detector.py -v

# Via Wrapper
python tests/run_quality_gates.py
python tests/run_quality_gates.py --fast
```

## Was getestet wird

| Datei | Was sie prüft | Anzahl |
|---|---|---|
| `test_constraints_yaml.py` | YAML parst, kanonische Sektionen vorhanden, Werte korrekt | 7 |
| `test_constraints_in_code.py` | bot.py-Konstanten = constraints.yaml-Werte (Drift-Detection) | 5 |
| `test_pattern_detector.py` | detect_bull_flag mit synthetischen Bars: alle Edge-Cases | 9 |
| `test_risk_engine.py` | Position-Sizing, Daily-Caps, Spiral-Lock, Time-Cuts | 9 |
| `test_replay_regression.py` | Replay 2026-04-15 muss exakt $12.15 PnL produzieren | 3 |
| `test_pilot_baseline.py` | Pilot-Backtest-Stats unverändert (V1/V2 trades, candidates sane) | 3 |
| **Total** | **36 Tests** | |

## Critical Tests (dürfen NIE fehlschlagen)

1. `test_replay_2026_04_15_baseline` — wenn Backtest-Output sich ändert, ist Code drifted
2. `test_price_below_min_rejected` — Bug-Fix-Regression-Test (HUBC bei $0.17)
3. `test_constraints_in_code.*` — wenn Code von YAML divergiert, Single-Source-of-Truth gebrochen
4. `test_can_enter_blocked_after_daily_max_loss` — Risk-Engine darf nie Daily-Max ignorieren

## Workflow nach Code-Änderung

```bash
# 1. Änderung gemacht
git diff

# 2. Tests laufen
python -m pytest tests/ -v --tb=short

# 3. Wenn alles grün → commit
git add -A && git commit -m "..."

# 4. Wenn rot → fix, repeat 2
```

## Pre-Commit-Hook (optional)

Kopiert nach `.git/hooks/pre-commit` damit Tests **automatisch** vor jedem
Commit laufen. Wenn rot → Commit blocked.

```bash
#!/usr/bin/env bash
echo "Running quality gates..."
python -m pytest tests/ -q --tb=line
if [ $? -ne 0 ]; then
    echo ""
    echo "QUALITY GATE FAILED — commit aborted"
    echo "Run 'python -m pytest tests/ -v' for details"
    exit 1
fi
```

Aktivieren: `chmod +x .git/hooks/pre-commit`

## Was NEUE Tests brauchen

| Wenn du änderst… | Schreibe Test in… |
|---|---|
| `bot.py:detect_bull_flag` | `test_pattern_detector.py` (synthetic-bar test pro neuer Bedingung) |
| `bot.py:compute_position_size` oder `can_enter_new` | `test_risk_engine.py` |
| `constraints.yaml` Strukturänderung | `test_constraints_yaml.py` |
| Neue Cameron-Constraint im Code | `test_constraints_in_code.py` (Cross-Check) |
| Neue Strategie-Logik mit Backtest-Effekt | `test_pilot_baseline.py` (neue Baseline-Stat aufnehmen) |

## Performance-Targets

- Full Suite: **< 5 Min**
- Fast Suite: **< 30 Sek** (nur unit-tests, ohne replay/scanner)
- Pre-Commit: **< 1 Min** (nur kritische tests)

Wenn Suite langsamer wird → Tests parallelisieren mit `pytest -n auto` (pytest-xdist).
