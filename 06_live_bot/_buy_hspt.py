"""Manual Bracket-Buy für HSPT."""
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

SYM = "HSPT"
snap = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=[SYM]))[SYM]
last = snap.latest_trade.price
hi = snap.daily_bar.high
lo = snap.daily_bar.low

print(f"HSPT live: last ${last:.2f}  day-high ${hi:.2f}  day-low ${lo:.2f}")

# Bracket-Parameter
# Entry: marketable limit bei last+0.05
# Stop: prev_close oder day-low minus 2c, was tiefer ist (max ~6% Risk)
prev = snap.previous_daily_bar.close
stop = round(max(lo - 0.05, prev - 0.05), 2)
entry = round(last + 0.05, 2)
risk_per_share = entry - stop
if risk_per_share <= 0:
    print("STOP > ENTRY — abort")
    sys.exit(1)

# T2 = 1:2 R:R
t2 = round(entry + 2 * risk_per_share, 2)
# Position: $50 max risk, capped 5 shares für Sicherheit
max_shares_risk = int(50 / risk_per_share)
shares = max(1, min(max_shares_risk, 5))

print(f"\nBracket-Plan:")
print(f"  Entry-Limit:  ${entry:.2f}")
print(f"  Stop:         ${stop:.2f}  (risk/share ${risk_per_share:.2f})")
print(f"  Take-Profit:  ${t2:.2f}  (1:2 R:R)")
print(f"  Shares:       {shares}")
print(f"  Total Risk:   ${shares * risk_per_share:.2f}")
print(f"  Total Cost:   ${shares * entry:.2f}")

# Asset-Check
a = tc.get_asset(SYM)
if not a.tradable:
    print(f"FAIL: {SYM} not tradable")
    sys.exit(1)
if not a.shortable:
    print(f"  note: {SYM} not shortable (Long-only OK)")

# Submit Bracket
print(f"\nSubmitting bracket order...")
try:
    o = tc.submit_order(LimitOrderRequest(
        symbol=SYM, qty=shares, side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        limit_price=entry,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=t2),
        stop_loss=StopLossRequest(stop_price=stop),
    ))
    print(f"  order_id: {o.id}")
    print(f"  status:   {o.status}")
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

# Poll for fill
print(f"\nWaiting for fill...")
for i in range(20):
    time.sleep(1)
    o = tc.get_order_by_id(o.id)
    if str(o.status) in ("OrderStatus.FILLED", "filled"):
        print(f"  ✓ FILLED @ ${float(o.filled_avg_price):.4f} after {i+1}s")
        print(f"\nPosition open. Stop+TP active broker-side.")
        break
    if str(o.status) in ("OrderStatus.CANCELED", "OrderStatus.REJECTED"):
        print(f"  ✗ {o.status}")
        sys.exit(1)
    print(f"  {i+1}s: {o.status}")
else:
    print("  TIMEOUT after 20s — order still pending")
