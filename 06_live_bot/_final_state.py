import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_alpaca_keys
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from datetime import datetime, timezone, timedelta

k, s = get_alpaca_keys()
c = TradingClient(k, s, paper=True)
print(f"positions open: {len(c.get_all_positions())}")
print(f"equity: ${float(c.get_account().equity):,.2f}")
since = datetime.now(timezone.utc) - timedelta(minutes=15)
recent = c.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, after=since, limit=30))
print(f"\nrecent filled orders ({len(recent)}):")
for o in recent:
    if o.filled_avg_price:
        ts = str(o.created_at)[11:19]
        print(f"  {ts} {o.side} {o.qty} {o.symbol} @ ${float(o.filled_avg_price):.4f} ({o.status})")
