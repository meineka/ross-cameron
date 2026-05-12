"""Bracket-Buys für mehrere Symbole."""
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

# Cameron-Setups: Stop ~5% below ask, TP at 1:2 R:R
TARGETS = ["BBIG", "ATRA", "SAVA"]
SHARES_PER = 5  # bewusst klein für paper-test

print("=" * 70)
snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=TARGETS))
order_ids = {}

for sym in TARGETS:
    sn = snaps.get(sym)
    if not sn:
        print(f"{sym}: no snapshot — skip")
        continue
    last = sn.latest_trade.price
    ask = sn.latest_quote.ask_price or last
    bid = sn.latest_quote.bid_price or last
    spread = ask - bid if ask > 0 and bid > 0 else 0
    print(f"\n{sym}  last ${last:.2f}  bid ${bid:.2f}  ask ${ask:.2f}  spread {spread:.3f}")

    # Tradable check
    try:
        a = tc.get_asset(sym)
        if not a.tradable:
            print(f"  not tradable — skip"); continue
    except Exception as e:
        print(f"  asset-check fail: {e}"); continue

    entry = round(max(ask, last) + 0.02, 2)
    stop  = round(entry * 0.95, 2)        # ~5% Stop
    t2    = round(entry + 2 * (entry - stop), 2)  # 1:2 R:R
    risk  = entry - stop
    print(f"  entry ${entry:.2f}  stop ${stop:.2f}  TP ${t2:.2f}  risk/share ${risk:.2f}")
    print(f"  shares {SHARES_PER}  total risk ${SHARES_PER*risk:.2f}  cost ${SHARES_PER*entry:.2f}")

    try:
        o = tc.submit_order(LimitOrderRequest(
            symbol=sym, qty=SHARES_PER, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=entry,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=t2),
            stop_loss=StopLossRequest(stop_price=stop),
        ))
        order_ids[sym] = o.id
        print(f"  submitted {str(o.id)[:8]} status={o.status}")
    except Exception as e:
        print(f"  FAIL: {e}")

# Wait for fills
print("\n" + "=" * 70)
print("WAITING FOR FILLS")
print("=" * 70)
fills = {}
for _ in range(20):
    time.sleep(1)
    pending = [s for s in order_ids if s not in fills]
    if not pending: break
    for sym in pending:
        try:
            o = tc.get_order_by_id(order_ids[sym])
            if str(o.status) in ("OrderStatus.FILLED", "filled"):
                fills[sym] = float(o.filled_avg_price)
                print(f"  ✓ {sym} filled @ ${fills[sym]:.4f}")
        except Exception:
            pass

print(f"\nFilled {len(fills)} / {len(order_ids)}")
for sym, p in fills.items():
    print(f"  {sym}: 5 shares @ ${p:.4f}  =  ${5*p:.2f}")
