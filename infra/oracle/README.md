# Oracle Cloud Always Free — Cameron-Bot 24/7 Deploy

User-side steps (you do these once, ~30 min):

## 1. Sign up Oracle Cloud Always Free

1. https://www.oracle.com/cloud/free/ → **Start for free**
2. Region: pick one with ARM availability (e.g. `Frankfurt`, `Amsterdam`,
   `Zurich` for EU). ARM Ampere A1 VMs are the free 24/7 option.
3. CC verification needed (no charge — required for identity check). Pick
   "Always Free" tier; never upgrade to paid.

## 2. Provision the VM

1. Console → **Compute → Instances → Create Instance**
2. Name: `cameron-bot`
3. Image: **Ubuntu 22.04 ARM** (Canonical) — NOT x86_64
4. Shape: **VM.Standard.A1.Flex**, 2 OCPU, 12 GB RAM (well within Always
   Free quota of 4 OCPU + 24 GB total)
5. Networking: default VCN, **Assign public IPv4** = YES
6. SSH key: paste your public key (`~/.ssh/id_ed25519.pub` content)
7. Create. Wait ~2 min until State=Running.

## 3. SSH in + run the deploy script

```bash
# Copy public IP from Oracle console
ssh ubuntu@<public-ip>

# On the VM:
curl -fsSL https://raw.githubusercontent.com/meineka/ross-cameron/master/infra/oracle/setup.sh | bash
```

This script:
- Updates apt, installs python3.11, git, curl
- Clones the repo to `/opt/ross-cameron`
- Installs Python deps from requirements.txt
- Creates `/opt/ross-cameron/06_live_bot/.env` template
- Installs systemd unit `cameron-bot.service`
- Does NOT start the bot yet — you fill in .env first

## 4. Configure secrets

```bash
sudo -e /opt/ross-cameron/06_live_bot/.env
```

Set the 3 lines:
```
APCA_API_KEY_ID=PK...
APCA_API_SECRET_KEY=...
NTFY_TOPIC=cameron-bot-ysdsphiehndewxp
STRATEGY_VARIANT=loose
SKIP_HARD_FLAT_TODAY=1
```

## 5. Start the bot daemon (24/7)

```bash
sudo systemctl enable --now cameron-bot
sudo systemctl status cameron-bot
journalctl -u cameron-bot -f          # tail live logs
```

The systemd unit auto-restarts on crash, survives reboot, runs forever.

## 6. Open ntfy.sh ports (already open, nothing to do)

Outbound HTTPS to ntfy.sh is allowed by default. No iptables changes.

## 7. Verify (within 60 sec)

You should receive on phone:
- **Bot started** push within 30 sec of `systemctl start`
- **🟢 HEARTBEAT t=1m** within 60 sec
- continuous heartbeats every 60 sec

If not: `journalctl -u cameron-bot --since "5 min ago" | grep -E "(alerter|ntfy|HEARTBEAT)"`

## Comparison: Oracle Always Free vs GitHub Actions

| | GitHub Actions | Oracle Always Free |
|---|---|---|
| Runtime per session | 6h max | Unlimited (24/7) |
| Scheduled cron | Unreliable (7h delays) | Cron native (`crontab -e`) reliable |
| Cost | $0 (free tier) | $0 forever |
| RAM | 7 GB | 12 GB (or up to 24 GB) |
| Reboot survival | No (ephemeral) | Yes (systemd) |
| Pre-market scan window | Skipped (6h budget) | Full 9:30 ET coverage |
| Multi-day state | Lost between runs | Persistent on disk |

Once Oracle is verified working, the GitHub Actions workflow can stay
as a backup or be deactivated.
