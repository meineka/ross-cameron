# GO-LIVE — Morgen Demo-Trading

## HEUTE ABEND (1× Setup, 30 Min)

### Step 1 — Alpaca-Paper-Account (15 Min)
- [ ] https://alpaca.markets/signup
- [ ] Email + DE-Adresse + Pass-Foto hochladen
- [ ] Verification-Email abwarten (meist <1h, max 12h)
- [ ] **Wichtig**: Im Dashboard oben rechts auf "Paper Trading" wechseln (NICHT Live!)

### Step 2 — API-Keys (5 Min)
- [ ] https://app.alpaca.markets/paper/dashboard/overview
- [ ] Sektion "API Keys" rechts unten → "Generate New Key"
- [ ] **Sofort kopieren** (Secret wird NUR 1× gezeigt):
  ```
  APCA_API_KEY_ID:      PK...
  APCA_API_SECRET_KEY:  langer-string
  ```
- [ ] In Password-Manager speichern

### Step 3 — Environment-Variables (5 Min)

**PowerShell als Admin:**
```powershell
[Environment]::SetEnvironmentVariable("APCA_API_KEY_ID", "PKxxxxx", "User")
[Environment]::SetEnvironmentVariable("APCA_API_SECRET_KEY", "yyyyyy", "User")
```

**NEUE PowerShell aufmachen** (alte sieht die Variablen nicht):
```powershell
echo $env:APCA_API_KEY_ID    # Test, muss PK... zeigen
```

### Step 4 — Pre-Flight-Tests (5 Min)

```bash
cd C:\Users\Szymon\ross-cameron\06_live_bot

# 1) Alpaca-Verbindung
python bot.py --check-connection
# Erwartet: Status ACTIVE, Equity $100k, Trading-Block False

# 2) Quality-Gates (fast)
cd ..
python -m pytest tests/ -q --ignore=tests/test_replay_regression.py

# 3) Scanner
cd 06_live_bot
python bot.py --scan-only
# Erwartet: Top-10 für heute

# 4) Replay (Bot-Logik gegen historische Daten)
python bot.py --replay 2026-04-15
# Erwartet: 3 Trades, Daily realized PnL: $12.15
```

→ Wenn alle 4 grün: **bereit für morgen**.

## MORGEN — Live-Run

### 12:30 CET (06:30 ET) — Pre-Market

```bash
cd C:\Users\Szymon\ross-cameron\06_live_bot
python bot.py --scan-only > today_watchlist.txt
notepad today_watchlist.txt
```

→ Schau dir an WAS gehandelt wird. Wenn die Liste komisch aussieht (lauter Penny-Stocks, kein klarer Move) → **NICHT live gehen**, lieber `--replay` machen.

### 13:00 CET (07:00 ET) — Bot starten

```bash
cd C:\Users\Szymon\ross-cameron\06_live_bot
python bot.py
```

→ Bot läuft jetzt autonom bis 18:00 CET.

### Während der Session — Status-Check (jede Stunde)

In ZWEITER PowerShell (Bot-Session NICHT unterbrechen):
```bash
python bot.py --status
```

→ Zeigt: Equity, offene Positions, heutige Orders, P&L.

### Notfall-Stop

```
In Bot-Session: Ctrl+C
ODER: https://app.alpaca.markets/paper → "Close All Positions"
```

### 18:00 CET — Bot stoppt automatisch

Bot macht `market_close_all()` und beendet sich. Du machst:

```bash
# Trade-Log angucken
type trades_live.jsonl

# Alpaca-Dashboard für Tagesbilanz
start https://app.alpaca.markets/paper/dashboard/overview
```

## WICHTIG für Day-1

**Erste Erwartung**: 0-3 Trades, $0-100 Paper-PnL, möglicherweise nichts wenn keine Setups feuern.

**Was bedeutet GUT:**
- Bot startete sauber
- Watchlist machte Sinn
- Pattern-Detection lief ohne Crashes
- Wenn Trade kam: Stop/Target funktionierten

**Was bedeutet PROBLEM:**
- Bot crasht / Connection-Errors
- Watchlist hat Stocks ohne Movement
- Trades feuern obwohl Patterns nicht da
- Alpaca lehnt Orders ab

→ Im Problem-Fall: `bot.log` an mich (Screenshots oder Datei), ich debugge.

## Hard-Caps die heute aktiv sind (in bot.py)

```python
MAX_LOSS_PER_TRADE_USD = 50.0      # max $50 Verlust pro Trade
DAILY_MAX_LOSS_USD = 150.0          # max $150 Verlust am Tag → STOP
DAILY_GOAL_USD = 150.0              # Goal symmetric
```

→ Bei $100k Paper-Konto völlig konservativ. Anpassen können wir wenn alles läuft.

## Nach Tag 1 — Was wir analysieren

```bash
python -c "
import json, pandas as pd
events = [json.loads(l) for l in open('06_live_bot/trades_live.jsonl')]
df = pd.DataFrame(events)
print(df.groupby('event').size())
print(df[df['event']=='entry'].groupby('rank')['symbol'].count())
"
```

→ Wieviele Setups detektiert, wieviele tatsächlich entered, welche Ranks.

## Was ich brauche von dir HEUTE ABEND

1. ✓ Alpaca-Account angelegt (Screenshot Dashboard)
2. ✓ `python bot.py --check-connection` Output
3. ✓ Alle 4 Pre-Flight-Tests grün

Dann: morgen Bot starten, ich bin standby für Probleme.

## Was ich brauche von dir MORGEN ABEND

1. `bot.log` (Bot-Output)
2. `trades_live.jsonl` (Trade-Events)
3. Screenshot Alpaca-Dashboard (heute-PnL)

Dann reviewen wir Tag-1 + planen Tag-2.
