import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_alpaca_keys
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

K, S = get_alpaca_keys()
tc = TradingClient(K, S, paper=True)
dc = StockHistoricalDataClient(K, S)

print("=== POSITIONS ===")
for p in tc.get_all_positions():
    print(f"  {p.symbol}: {p.qty} @ avg ${float(p.avg_entry_price):.4f}")
    print(f"    market_value=${float(p.market_value):.2f}  current_price=${float(p.current_price):.4f}")
    print(f"    unrealized_pl=${float(p.unrealized_pl):+.2f}  ({float(p.unrealized_plpc)*100:+.2f}%)")

print("\n=== OPEN ORDERS (HSPT) ===")
opens = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=["HSPT"], limit=20))
for o in opens:
    print(f"  {str(o.id)[:8]} {o.side} {o.qty} {o.symbol} type={o.order_type} stop_price={o.stop_price} limit={o.limit_price} status={o.status}")

print("\n=== LIVE QUOTE ===")
snap = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=["HSPT"]))["HSPT"]
print(f"  last ${snap.latest_trade.price:.2f}")
print(f"  ask  ${snap.latest_quote.ask_price:.2f}  bid ${snap.latest_quote.bid_price:.2f}")
