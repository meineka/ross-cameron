"""Manual Bracket-Buy für AAPL — liquid, tight stop."""
import sys, io, time
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_alpaca_keys
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

K, S = get_alpaca_keys()
tc = TradingClient(K, S, paper=True)
dc = StockHistoricalDataClient(K, S)

SYM = "AAPL"
snap = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=[SYM]))[SYM]
last = snap.latest_trade.price
ask = snap.latest_quote.ask_price
bid = snap.latest_quote.bid_price
print(f"AAPL  last ${last:.2f}  bid ${bid:.2f}  ask ${ask:.2f}")

# Conservative bracket for liquid mega-cap
entry = round(ask + 0.05 if ask > 0 else last + 0.05, 2)
stop = round(entry - 1.50, 2)        # ~0.5% Stop
t2   = round(entry + 3.00, 2)        # ~1% TP, 1:2 R:R
risk_per_share = entry - stop
shares = 2                            # paper-test, ~$600
print(f"\nBracket plan:")
print(f"  Entry  ${entry:.2f}  Stop ${stop:.2f}  TP ${t2:.2f}")
print(f"  Shares {shares}   Risk/share ${risk_per_share:.2f}   Total risk ${shares*risk_per_share:.2f}")
print(f"  Total cost ~${shares*entry:.2f}")

o = tc.submit_order(LimitOrderRequest(
    symbol=SYM, qty=shares, side=OrderSide.BUY,
    time_in_force=TimeInForce.DAY,
    limit_price=entry,
    order_class=OrderClass.BRACKET,
    take_profit=TakeProfitRequest(limit_price=t2),
    stop_loss=StopLossRequest(stop_price=stop),
))
print(f"\nsubmitted {str(o.id)[:8]}  status={o.status}")
for i in range(15):
    time.sleep(1)
    o = tc.get_order_by_id(o.id)
    if str(o.status) in ("OrderStatus.FILLED", "filled"):
        print(f"  ✓ FILLED @ ${float(o.filled_avg_price):.4f} after {i+1}s")
        break
    print(f"  {i+1}s: {o.status}")
