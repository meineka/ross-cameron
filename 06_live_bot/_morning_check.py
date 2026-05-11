"""Pre-Morning-Check: state report für Tag-Start morgen."""
from __future__ import annotations
import sys, io, json
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from secrets_loader import get_alpaca_keys
from alpaca.trading.client import TradingClient
from pre_flight import run_preflight

print("=" * 60)
print("MORNING-PREP CHECK")
print("=" * 60)

k, s = get_alpaca_keys()
ok = run_preflight(k, s)
print()

c = TradingClient(k, s, paper=True)
a = c.get_account()
print(f"Account-Status:    {a.status}")
print(f"Equity:            ${float(a.equity):,.2f}")
print(f"Buying-Power:      ${float(a.buying_power):,.2f}")
print(f"Cash:              ${float(a.cash):,.2f}")
print(f"Day-trade-count:   {a.daytrade_count}")
print(f"Pattern-day-trade: {a.pattern_day_trader}")
print(f"Open-Positions:    {len(c.get_all_positions())}")
print()

bot_dir = Path(__file__).parent
files_to_check = [
    ("heartbeat.txt", "<60s alt"),
    (".env", "secrets"),
    ("watchlist_today.json", "von heute? (wenn vorhanden)"),
    ("status.json", "live-state (wenn vorhanden)"),
]
print("File-Inventory:")
for f, desc in files_to_check:
    p = bot_dir / f
    if p.exists():
        size = p.stat().st_size
        import time
        age = int(time.time() - p.stat().st_mtime)
        print(f"  ✓ {f:30s} {size:>8}B, {age}s old  — {desc}")
    else:
        print(f"  – {f:30s} (missing)  — {desc}")

print()
print(f"Pre-flight: {'PASS' if ok else 'FAIL'}")
print("=" * 60)
