# Reliability-Architektur — Cameron-Bot

## Schutz-Layer (von innen nach außen)

### Layer 1 — Bot-internes Self-Healing
- `daemon_run()` try/except um jeden Trading-Tag
- Wenn Trading-Tag-Crash → schläft bis nächster Tag, neuer Versuch
- WebSocket-Auto-Reconnect (mit Counter)
- Per-Bar-Try/Except (ein Bar-Fehler killt nicht den Bot)
- yfinance-Scan-Retry mit exponential backoff (max 2 retries)
- Premarket-Scan failed → empty watchlist, sauberer Skip

### Layer 2 — Bot-Heartbeat-File
- `heartbeat.txt` updated jede 60s mit aktuellem Timestamp
- Wenn >30 Min alt → Audit erkennt: `RESTART_HEARTBEAT_STALE`
- Externe Watchdogs sehen sofort "Bot lebt"

### Layer 3 — Watchdog (Monitor in Chat)
- 4 Monitore aktiv:
  - `bfyg91pgi` Trading-Events
  - `b6g1w9gt3` Heartbeat-Tick
  - `bnd5vfxoo` Process-Watchdog
  - `bjkblvfu6` Auto-Audit alle 5 Min
- Bei Problem: Notification → Claude diagnostiziert + fixt

### Layer 4 — Auto-Audit + Trade-Lock-Restart
- `audit.py` klassifiziert Errors (15 Categorien)
- `deploy_safe.py` checkt Position-Lock vor Restart
- Auto-Restart NUR wenn keine offenen Trades
- Wenn Position offen: warten, retry alle 5 Min

### Layer 5 — OS-Level
- Power-Settings: kein Sleep/Hibernate (powercfg)
- Auto-Start nach Reboot via Startup-Folder (`cameron_bot.bat`)
- 580 GB Disk frei (kein Risiko Disk-Full)

### Layer 6 — Network/Service
- Alpaca Paper-Endpoint reachable (ping-test OK)
- yfinance-Connection getestet (1.1s response)
- WebSocket-Reconnect bei Disconnects automatisch

## Fail-Modes & Recovery

| Failure | Detection | Recovery |
|---|---|---|
| Bot-Process crasht | Layer 3 Process-Watchdog → 5min | deploy_safe.py restart |
| Bot hängt (kein Heartbeat) | Layer 2 → audit `heartbeat_stale` | deploy_safe.py restart |
| WebSocket disconnect | Bot internal try/except | Auto-Reconnect (max ∞) |
| yfinance rate-limit | Bot retry-loop | exponential backoff |
| Alpaca API down | API-error in audit | warten, retry |
| Trading-day-crash | daemon-loop try/except | nächster Tag retry |
| PC-Sleep | n/a | Power-Settings off — sollte nicht passieren |
| PC-Reboot | Startup-Folder | Auto-Start cameron_bot.bat |
| Memory-Leak | audit memory-check | RESTART_MEMORY_HIGH bei >2GB |
| Disk-Full | audit disk-check | ALERT_DISK_LOW bei <1GB |
| Code-Bug | audit category=code_bug | Claude fixt + deploy_safe |

## Audit-Categories

| Category | Severity | Auto-Fix | Action |
|---|---|---|---|
| `yfinance_rate_limit` | low | ja | Bot wartet selbst |
| `ws_disconnect` | low | ja | Auto-reconnect |
| `network` | medium | ja | reconnect-Loop |
| `alpaca_auth` | high | nein | User-Alert |
| `no_buying_power` | high | nein | User-Alert |
| `asset_not_tradable` | low | ja | skip stock |
| `order_rejected` | medium | ja | log + skip |
| `code_bug` | critical | nein | Claude-Fix nötig |
| `empty_watchlist` | info | ja | normal (Holiday) |
| `spy_bear` | info | nein | by design |
| `goal_reached` | info | nein | good day |
| `spiral_lock` | warning | nein | by design |
| `max_trades` | info | nein | rate-limit |

## Recommendation Logic

```
not bot_alive               → RESTART_BOT_PROCESS_DEAD
critical_errors > 0          → FIX_CRITICAL_THEN_RESTART
memory > 2 GB                → RESTART_MEMORY_HIGH
disk < 1 GB                  → ALERT_DISK_LOW
high_severity > 3            → INVESTIGATE_HIGH_SEVERITY
log_stale > 20 min           → RESTART_LOG_STALE
heartbeat_stale > 30 min     → RESTART_HEARTBEAT_STALE
else                         → ok
```

## Recovery-Befehle (manuell, falls automated fails)

```bash
# Status-Check
cd C:\Users\Szymon\ross-cameron\06_live_bot
python audit.py

# Bot-Restart manuell
python deploy_safe.py            # blockt wenn Positions offen
# Force-Restart (DANGER if positions):
taskkill /F /IM python.exe
nohup python bot.py --daemon > daemon.log 2>&1 &

# Notfall: alle Positions schließen
python -c "
import os
from alpaca.trading.client import TradingClient
c = TradingClient(os.environ['APCA_API_KEY_ID'], os.environ['APCA_API_SECRET_KEY'], paper=True)
c.close_all_positions(cancel_orders=True)
print('All positions closed')
"
```

## Was im Live-Tag passiert (mit allen Layers)

```
12:27:00 CET — Bot wacht auf
  Layer 1: SPY-Trend-Filter → Size-Multiplier
  Layer 1: Premarket-Scan → 5-Pillars-Filter → Top-10
  Layer 4: Audit detects no errors → ok
13:00:00 CET — Live-Trading
  Layer 2: Heartbeat updates jede 60s
  Layer 4: Audit alle 5 Min check
  Layer 1: WebSocket connect → Bar-Streaming
  Layer 1: Pattern-Detection → Risk-Engine → Order
14:23:42 CET — Beispiel: NameError im handle_bar
  Layer 1: Per-Bar-Try/Except fängt's, log: ERROR
  Layer 4: Audit erkennt category=code_bug, severity=critical
  Layer 4: Claude in Chat sieht Notification
  Layer 4: Claude analysiert, fixt Code, runs tests
  Layer 4: deploy_safe.py: positions=0 → restart erlaubt
  Layer 4: Bot 5s Downtime, dann wieder online
14:23:50 CET — Trading läuft weiter
18:00:00 CET — Hard Flat
  Layer 1: market_close_all + day_summary
```

## Aktueller Stand (live)

```
Bot:               PID variabel (lebt aktuell)
Heartbeat-File:    aktualisiert alle 60s
Memory:            ~111 MB (gesund)
Disk free:         580 GB
Power-Settings:    kein Sleep
Auto-Start:        Startup-Folder verlinkt
4 Monitore:        aktiv im Chat
Audit-System:      alle 5 Min, Trade-Lock gesichert
GitHub:            synced
Tests:             36/36 grün
```

## Was DICH schützt vor Total-Failure

Das Konto ist Paper-Trading auf $100k. Maximaler Verlust pro Tag durch
Hard-Caps: $150 (0.15% des Konto). Selbst wenn ALLE Schutz-Layer komplett
versagen, ist das Risiko begrenzt durch Alpaca-Server-Side-Risk-Limits +
unsere Bot-internen Hard-Caps.
