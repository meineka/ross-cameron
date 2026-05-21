#!/usr/bin/env bash
# Oracle Cloud Always Free — Cameron-Bot deploy script
# Run as `ubuntu` user on a fresh Ubuntu 22.04 ARM VM:
#   curl -fsSL https://raw.githubusercontent.com/meineka/ross-cameron/master/infra/oracle/setup.sh | bash
#
# Idempotent: safe to re-run.

set -euo pipefail

REPO_URL="https://github.com/meineka/ross-cameron.git"
INSTALL_DIR="/opt/ross-cameron"
BOT_USER="botd"

echo "================================================================"
echo "Cameron-Bot Oracle Cloud Setup"
echo "Time: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
echo "Host: $(hostname)  Arch: $(uname -m)"
echo "================================================================"

# ─── 1. System packages ────────────────────────────────────────────
echo "[1/7] Installing system packages..."
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -yq \
    python3.11 python3.11-venv python3.11-dev \
    git curl ca-certificates \
    build-essential libssl-dev libffi-dev \
    sqlite3

# ─── 2. Dedicated service user (no shell, no sudo) ─────────────────
echo "[2/7] Creating service user '$BOT_USER'..."
if ! id "$BOT_USER" &>/dev/null; then
    sudo useradd --system --shell /usr/sbin/nologin --home "$INSTALL_DIR" "$BOT_USER"
fi

# ─── 3. Clone / update repo ────────────────────────────────────────
echo "[3/7] Fetching repo into $INSTALL_DIR..."
if [ -d "$INSTALL_DIR/.git" ]; then
    sudo -u "$BOT_USER" git -C "$INSTALL_DIR" fetch origin master
    sudo -u "$BOT_USER" git -C "$INSTALL_DIR" reset --hard origin/master
else
    sudo mkdir -p "$INSTALL_DIR"
    sudo chown "$BOT_USER:$BOT_USER" "$INSTALL_DIR"
    sudo -u "$BOT_USER" git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# ─── 4. Python venv + deps ─────────────────────────────────────────
echo "[4/7] Setting up Python venv..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    sudo -u "$BOT_USER" python3.11 -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$BOT_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$BOT_USER" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# ─── 5. .env template (if not exists) ──────────────────────────────
ENV_FILE="$INSTALL_DIR/06_live_bot/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "[5/7] Creating .env template..."
    sudo -u "$BOT_USER" tee "$ENV_FILE" > /dev/null <<'EOT'
# Cameron-Bot env file — FILL THESE IN BEFORE STARTING
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
NTFY_TOPIC=
# Strategy: strict | relaxed | loose | ultra | force
STRATEGY_VARIANT=loose
# 1 = trade until 15:55 ET HARD_FLAT (recommended for cloud)
SKIP_HARD_FLAT_TODAY=1
EOT
    sudo chmod 600 "$ENV_FILE"
    sudo chown "$BOT_USER:$BOT_USER" "$ENV_FILE"
    echo "  ⚠️  Now edit $ENV_FILE and fill in APCA_API_KEY_ID + APCA_API_SECRET_KEY + NTFY_TOPIC"
else
    echo "[5/7] .env exists, keeping current contents"
fi

# ─── 6. systemd unit ───────────────────────────────────────────────
echo "[6/7] Installing systemd unit..."
sudo tee /etc/systemd/system/cameron-bot.service > /dev/null <<EOT
[Unit]
Description=Cameron Bull-Flag Trading Bot (Oracle Cloud 24/7)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$INSTALL_DIR/06_live_bot
EnvironmentFile=$INSTALL_DIR/06_live_bot/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -u bot.py --daemon
Restart=on-failure
RestartSec=30
# Don't kill child processes on shutdown; let bot do its HARD_FLAT
KillSignal=SIGTERM
TimeoutStopSec=60
# Resource limits (ARM Ampere A1 plenty)
MemoryHigh=2G
MemoryMax=3G
CPUQuota=200%
# Logs go to systemd journal — no on-disk log explosion
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOT
sudo systemctl daemon-reload

# ─── 7. Update helper (for future deploys) ─────────────────────────
echo "[7/7] Installing update helper..."
sudo tee /usr/local/bin/cameron-bot-update > /dev/null <<EOT
#!/bin/bash
set -e
cd $INSTALL_DIR
sudo -u $BOT_USER git fetch origin master
sudo -u $BOT_USER git reset --hard origin/master
sudo -u $BOT_USER $INSTALL_DIR/.venv/bin/pip install -r requirements.txt --upgrade
sudo systemctl restart cameron-bot
sudo systemctl status cameron-bot --no-pager
EOT
sudo chmod +x /usr/local/bin/cameron-bot-update

echo ""
echo "================================================================"
echo "SETUP COMPLETE"
echo "================================================================"
echo ""
echo "Next steps:"
echo "  1. Edit secrets:    sudo nano $ENV_FILE"
echo "  2. Start daemon:    sudo systemctl enable --now cameron-bot"
echo "  3. Tail logs:       journalctl -u cameron-bot -f"
echo "  4. Check status:    sudo systemctl status cameron-bot"
echo "  5. Future updates:  cameron-bot-update"
echo ""
echo "Within 60s of start, you should receive on phone:"
echo "  - 'Bot started' ntfy push"
echo "  - '🟢 HEARTBEAT t=1m' ntfy push (then every 60s)"
