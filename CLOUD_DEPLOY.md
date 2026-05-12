# Cloud-Deploy Optionen — Bot 24/7 weiterlaufen lassen

Du musst den PC ausmachen. Bot soll trotzdem morgen 12:27 CET starten.
Hier die realistischen Optionen, ranked nach **kann morgen früh starten**.

## ⚡ Option A: Fly.io (empfohlen)

**Warum:** Free-Tier 3 small VMs, git-push-deploy, persistent volume.
**Kosten:** $0 bis ~$2/Monat (im Free-Tier-Limit).
**Setup-Zeit:** ~15-20 Min.

```bash
# 1. flyctl installieren
iwr https://fly.io/install.ps1 -useb | iex   # Windows PowerShell

# 2. Login (öffnet Browser)
flyctl auth signup    # neu, oder
flyctl auth login

# 3. App-Init im Repo
cd C:\Users\Szymon\ross-cameron
flyctl launch --no-deploy
# → wählt Region (z.B. ams = Amsterdam), keine DB

# 4. Dockerfile erstellen (siehe unten)
# 5. fly.toml anpassen (siehe unten)
# 6. Secrets setzen
flyctl secrets set APCA_API_KEY_ID="DEIN_KEY"
flyctl secrets set APCA_API_SECRET_KEY="DEIN_SECRET"

# 7. Deploy
flyctl deploy
```

### Dockerfile (im Repo-Root)
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY 06_live_bot ./06_live_bot
COPY 03_rules_engine ./03_rules_engine
WORKDIR /app/06_live_bot
CMD ["python", "bot.py", "--daemon"]
```

### fly.toml (im Repo-Root)
```toml
app = "cameron-bot"
primary_region = "ams"

[build]
  dockerfile = "Dockerfile"

[[mounts]]
  source = "bot_data"
  destination = "/app/06_live_bot/data"

[[vm]]
  size = "shared-cpu-1x"
  memory = "256mb"

[processes]
  app = "python bot.py --daemon"
```

### requirements.txt (im Repo-Root) — falls noch nicht da
```
alpaca-py>=0.40
pyyaml
pandas
numpy
yfinance
websockets
```

---

## 🥈 Option B: Railway

**Warum:** GitHub-Deploy, sehr einfach, hat $5 free monthly credit.
**Kosten:** ~$3-5/Monat nach Free-Credit.
**Setup-Zeit:** ~10 Min.

1. railway.app → Sign-up (GitHub-Login)
2. New Project → Deploy from GitHub
3. Wähle `meineka/ross-cameron`
4. Settings → Variables:
   - `APCA_API_KEY_ID`
   - `APCA_API_SECRET_KEY`
5. Settings → Service → Start Command:
   ```
   cd 06_live_bot && python bot.py --daemon
   ```
6. Deploy

Railway erkennt Python automatisch. Kein Dockerfile nötig.

---

## 🥉 Option C: VPS (Hetzner / DigitalOcean / Contabo)

**Warum:** Volle Kontrolle, fixe Kosten.
**Kosten:** Hetzner CX11 €4.51/Monat (ARM €3.79).
**Setup-Zeit:** ~30 Min.

```bash
# auf VPS einloggen via SSH
ssh root@your-vps-ip

apt update && apt install -y python3.11 python3-pip git tmux
git clone https://github.com/meineka/ross-cameron.git
cd ross-cameron
pip install -r requirements.txt

# .env erstellen mit deinen Keys
cat > 06_live_bot/.env <<EOF
APCA_API_KEY_ID=DEIN_KEY
APCA_API_SECRET_KEY=DEIN_SECRET
EOF

# in tmux starten (persistent über SSH-Disconnect)
tmux new -s bot
cd 06_live_bot
python bot.py --daemon
# Ctrl+B dann D → detached, läuft weiter
```

Für Auto-Restart: systemd-Service-File.

---

## 🚫 Option D: GitHub Codespaces

**Schlecht für 24/7:** Codespaces stoppen automatisch nach 30 Min Inaktivität.
Bot würde nicht durchgehen. **Nicht empfohlen.**

---

## ⚠️ Wichtige Punkte zum Bot in der Cloud

### 1. Alpaca-Paper-Account
- Paper-Trading läuft serverseitig bei Alpaca — Cloud-Bot greift via REST/WS zu
- Funktioniert weltweit von jeder Region

### 2. Zeit-Zone
- Bot hat NY_TZ hardcoded → funktioniert egal wo der Container läuft
- Container-Zeit muss korrekt sein (UTC ist üblich)

### 3. yfinance-Rate-Limits
- Cloud-IPs werden manchmal aggressiver rate-limited als Home-IPs
- Wir haben delisted-cache + WARNING-only Pre-Flight → tolerant

### 4. Logs
- Fly.io: `flyctl logs` für live tail
- Railway: Dashboard zeigt Live-Logs
- VPS: `tail -f 06_live_bot/daemon.log`

### 5. Persistente Daten
- `heartbeat.txt`, `watchlist_today.json`, `results/`, `delisted_cache.json`
- **Volume/Mount nötig** sonst alles weg bei Restart
- Fly.io: `[[mounts]]` block oben
- Railway: persistent storage in dashboard
- VPS: einfach im Filesystem

### 6. Monitoring
- Du verlierst die 4 Monitor-Notifications die wir lokal haben
- Alternative: Fly.io / Railway zeigen Logs im Dashboard
- Oder: Telegram-Bot für critical-events (kleiner Aufwand zum implementieren)

---

## 🎯 Schnellste Empfehlung für morgen früh

**Mach Option B (Railway):**

1. https://railway.app → Sign-up mit GitHub
2. "Deploy from Repo" → `meineka/ross-cameron`
3. Bei Build-Fehler: füge `requirements.txt` und `Procfile` hinzu:
   ```
   # Procfile
   worker: cd 06_live_bot && python bot.py --daemon
   ```
4. Variables setzen (Key + Secret)
5. Deploy

In ~10 Minuten läuft der Bot. Morgen 12:27 CET startet automatisch der Scan.

---

## Lokal jetzt vorbereiten (5 Min)

Damit Railway/Fly.io sofort deployen können, brauchen wir die fehlenden
Build-Files. Soll ich sie erstellen?
- `Dockerfile`
- `requirements.txt`
- `Procfile`
- `fly.toml` falls Fly.io
