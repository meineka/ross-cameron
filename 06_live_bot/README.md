# Cameron-Live-Bot (Alpaca Paper)

Vollautomatisierter Bot für Cameron's Bull-Flag-Strategie auf US-Stocks.
Top-10 Ranking, Pattern-Detection, Position-Management — alles automatisch.

## Was er macht

1. **12:30 CET** (06:30 ET): Pre-Market-Scanner
   - yfinance pullt alle US-Tickers
   - Filter Cameron-5-Pillars: Preis $2-20, +10% intraday, RVOL ≥ 2×
   - Composite-Score (RVOL × Daily-%) Ranking
   - Top-10 als Watchlist

2. **Live-Trading 13:00–17:30 CET** (07:00–11:30 ET):
   - Alpaca-WebSocket-Stream für Top-10 Tickers
   - Bull-Flag-Pattern-Detector pro neuem 5-min-Bar
   - Risk-Engine: Quarter-Size-Rule, Daily-Max-Loss, Spiral-Detection
   - Auto-Buy bei Signal (Limit-Order +1¢ Offset)
   - Auto-Exit: 50 % bei T1, 50 % bei T2, BE-Stop nach T1

3. **18:00 CET** (12:00 ET): Hard-Flat — alle Positions geschlossen

## Setup (15 Min)

### 1. Alpaca-Paper-Account anlegen
- Gehe auf https://app.alpaca.markets/signup
- Auch deutsche Adresse OK
- Default-Modus ist Paper-Trading (kein echtes Geld)
- $100k Paper-Money kommt automatisch
- Im Dashboard: API-Keys generieren (Paper-Trading-Section)

### 2. Environment-Variables setzen

PowerShell:
```powershell
$env:APCA_API_KEY_ID = "PKxxxxxxxxxxx"
$env:APCA_API_SECRET_KEY = "yourSecretHere"
```

Bash:
```bash
export APCA_API_KEY_ID="PKxxxxxxxxxxx"
export APCA_API_SECRET_KEY="yourSecretHere"
```

Permanent: in `.bashrc` / `.zshrc` / Windows-Umgebungsvariablen einfügen.

### 3. Test-Modi (in dieser Reihenfolge)

```bash
cd ross-cameron/06_live_bot

# A) Pure Scanner — keine API nötig
python bot.py --scan-only

# B) Replay-Modus — Bot-Logik gegen historische Pilot-Daten
python bot.py --replay 2026-04-15
python bot.py --replay 2026-05-06

# C) Live-Paper-Trading — braucht Alpaca-Keys
python bot.py
```

## Hard-Caps (in der `bot.py`-Source angepasst werden)

```python
MAX_LOSS_PER_TRADE_USD = 50.0      # konservativer Start
DAILY_MAX_LOSS_USD = 150.0          # = 3× max-loss-per-trade
DAILY_GOAL_USD = 150.0              # symmetric zur Cameron-Rule
INTRADAY_DRAWDOWN_PCT_OF_PROFITS = 50.0
QUARTER_SIZE_UNLOCK_CENTS = 0.20    # nach +20¢/Aktie kumuliert: full size

# Time-Cuts (NY-Time)
TIME_NEW_ENTRIES_END = 11:30        # keine neuen Entries
TIME_HARD_FLAT = 12:00              # alles flat
```

## Was tracked wird (`trades_live.jsonl`)

Jeder Event als JSON-Line:
```json
{"ts": "2026-05-10T13:30:00Z", "event": "watchlist", "symbol": "ANPA", "rank": 1, "score": 1363.8}
{"ts": "2026-05-10T13:42:00Z", "event": "entry", "symbol": "TRAW", "rank": 2, "entry_price": 5.43, "stop_price": 5.15, "shares": 178}
{"ts": "2026-05-10T13:48:00Z", "event": "T1", "symbol": "TRAW", "shares": 89, "price": 5.71}
{"ts": "2026-05-10T13:55:00Z", "event": "T2_exit", "symbol": "TRAW", "shares": 89, "price": 5.99}
```

→ Per-Rank-Stats nachträglich auswertbar mit `pandas.read_json(lines=True)`.

## Live-Validation deiner Sweet-Spot-Hypothese

Pilot-Stats sagten: **Rang 2-7 ist besser als Rang 1**.
Bot trackt jeden Trade mit `rank` → nach 1 Woche live ist sichtbar:
- Welche Rank-Bucket wirklich performte
- Ob Live-Pattern-Match Backtest entspricht

Nach 1 Woche: `python analyze_live.py` (TODO bauen) → per-rank P&L.

## Realistic Erwartungen 1. Woche

- 5 Trading-Tage
- 1-3 Trades pro Tag (1 Cameron-Style)
- 5-15 Trades total
- Statistisch nicht aussagekräftig (Win-Rate kann 30-90 % schwanken)
- **Hauptziel**: Workflow-Validation, nicht Edge-Beweis

## Bekannte Limitierungen

| Limit | Workaround |
|---|---|
| Alpaca Free = IEX-Feed (nur ~3% Volume) | upgrade auf Algo Trader Plus $99 für SIP |
| Yfinance Premarket-Volume unzuverlässig | OK für initial-Scan, IEX-Stream übernimmt RTH |
| Kein Catalyst-NLP | nur 5-Pillars-Filter, kein Tweet/News-Check |
| Kein Tape-Reading | Volume-Profile-Approximation |
| Kein Halt-Detection-Live | manuelle Pause bei Halt-Up sinnvoll |

## Kill-Switch

```bash
# Stoppt Bot SOFORT, schließt alle Positions
Ctrl+C

# Oder via Alpaca-Web-Dashboard:
# https://app.alpaca.markets/paper/dashboard/positions → "Close All"
```

## Daily-Check-In-Routine

**Morgens 12:30 CET (vor Open):**
```bash
python bot.py --scan-only > today_watchlist.txt
# → manuell prüfen: macht die Liste Sinn? alle Symbole bekannt?
```

**13:00 CET (Open):**
```bash
python bot.py
# → läuft autonomous bis 18:00 CET
```

**18:30 CET (nach Close):**
- Alpaca-Dashboard: P&L des Tages, alle Trades
- `tail trades_live.jsonl` für Bot-Log
- Journal-Eintrag

## Files

- `bot.py` — Single-File-Bot (~600 LOC)
- `bot.log` — Detailed Logging
- `trades_live.jsonl` — Per-Event-Log für Analyse
- `README.md` — diese Datei
