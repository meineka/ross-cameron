#!/usr/bin/env bash
# Cameron-Bot Oracle Cloud — Quick Status Check
# Run on Oracle VM as any user. No args.
#
# Prints:
#   - Service status (running/stopped/failed)
#   - Last 20 log lines
#   - Heartbeat freshness (last push within 90s?)
#   - Position count (via Alpaca API)
#   - Day P/L

set +e

echo "=== Cameron-Bot Oracle Cloud Status @ $(date -u +'%H:%M:%S UTC') ==="
echo ""

# 1. systemd
echo "--- Service ---"
systemctl is-active cameron-bot && echo "active=yes" || echo "active=no"
echo "uptime: $(systemctl show cameron-bot --property=ActiveEnterTimestamp --value)"
echo "restarts: $(systemctl show cameron-bot --property=NRestarts --value)"

# 2. Recent log
echo ""
echo "--- Last 15 log lines ---"
journalctl -u cameron-bot --no-pager -n 15

# 3. Heartbeat freshness
echo ""
echo "--- Heartbeat freshness ---"
LAST_HB=$(journalctl -u cameron-bot --no-pager -n 200 | grep -i "HEARTBEAT t=" | tail -1)
if [ -n "$LAST_HB" ]; then
    echo "$LAST_HB"
else
    echo "NO HEARTBEAT seen in last 200 log lines — bot may be stuck"
fi

# 4. Alpaca position check (via Python)
echo ""
echo "--- Alpaca account ---"
ENV_FILE="/opt/ross-cameron/06_live_bot/.env"
if [ -f "$ENV_FILE" ]; then
    /opt/ross-cameron/.venv/bin/python <<'PY' 2>/dev/null || echo "  (Alpaca query failed)"
import os
with open("/opt/ross-cameron/06_live_bot/.env") as f:
    for line in f:
        if "=" in line and not line.lstrip().startswith("#"):
            k,v=line.strip().split("=",1)
            os.environ[k]=v
from alpaca.trading.client import TradingClient
tc = TradingClient(os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"], paper=True)
a = tc.get_account()
p = tc.get_all_positions()
print(f"  Equity:        ${float(a.equity):,.2f}")
print(f"  Day P/L:       ${float(a.equity)-float(a.last_equity):+.2f}")
print(f"  Open positions: {len(p)}")
for pos in p:
    print(f"    {pos.symbol}: {pos.qty}sh @ ${float(pos.avg_entry_price):.2f} → ${float(pos.current_price):.2f} (P/L ${float(pos.unrealized_pl):+.2f})")
PY
fi

echo ""
echo "=== End status ==="
