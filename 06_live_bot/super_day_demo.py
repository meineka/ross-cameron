"""Super-Day-Demo: Top-10 Cameron-Setups jetzt live traden (1 share each).

Workflow:
  1. Snapshot über breites Universe (heutige WL + Cameron-Klassiker)
  2. Ranke nach Cameron-Score (RVOL × intraday_pct)
  3. Wähle die 10 besten (price $2-20, green, tradable)
  4. Market-Buy 1 share jeden
  5. Hold 10 s
  6. Close all positions
  7. PnL-Summary
"""
from __future__ import annotations
import os, sys, time, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from secrets_loader import get_alpaca_keys

KEY, SEC = get_alpaca_keys()
tc = TradingClient(KEY, SEC, paper=True)
dc = StockHistoricalDataClient(KEY, SEC)

# Breites Cameron-Universe: heutige WL + bekannte Low-Float-Runner + Volume-Movers
UNIVERSE = [
    # Heutige Top-10 (12:27 CET scan)
    "TRAW", "WTF", "WEST", "ANPA", "STFS", "CODX", "MASK", "RXT", "MTEX", "TC",
    # Cameron-Klassiker / häufige Runner
    "MARA", "RIOT", "CLSK", "BITF", "HUT",  # crypto-miner
    "SOFI", "PLTR", "RKT", "OPEN",            # fintech retail
    "AMC", "GME", "BBBY", "MULN",             # meme
    "AAPL", "TSLA",                            # mega-liquid fallback
]
HOLD_SECONDS = 10
TOP_N = 10

print("=" * 70)
print("SUPER-DAY-DEMO — Top-10 Cameron-Setups, 1 share each")
print("=" * 70)
print(f"Universe: {len(UNIVERSE)} candidates")

# 1. Snapshots
try:
    snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=UNIVERSE))
except Exception as e:
    print(f"FAIL snapshot: {e}")
    sys.exit(1)

# 2. Rank
ranked = []
for sym, snap in snaps.items():
    try:
        b = snap.daily_bar
        p = snap.previous_daily_bar
        last = snap.latest_trade
        if not (b and p and last and p.close > 0 and p.volume > 0):
            continue
        price = last.price
        pct = (price - p.close) / p.close * 100
        rvol = b.volume / max(p.volume, 1)
        score = rvol * max(pct, 0.1)
        green = b.close >= b.open
        in_range = 2 <= price <= 20
        ranked.append({
            "sym": sym, "price": price, "pct": pct,
            "rvol": rvol, "score": score, "green": green, "in_range": in_range,
        })
    except Exception as e:
        print(f"  {sym}: skip ({e})")

# Top-N by Cameron-Score (price $2-20 + green + positive pct)
ranked.sort(key=lambda r: -r["score"])
print(f"\nALL RANKED ({len(ranked)}):")
for r in ranked:
    flag = "✓" if r["in_range"] and r["green"] and r["pct"] > 0 else "✗"
    print(f"  {flag} {r['sym']:6s} ${r['price']:7.2f}  pct={r['pct']:+6.1f}%  rvol={r['rvol']:6.1f}  score={r['score']:7.0f}  green={r['green']}")

picks = [r for r in ranked if r["in_range"] and r["green"] and r["pct"] > 0][:TOP_N]
if len(picks) < TOP_N:
    # Fallback: nimm liquid green names auch wenn out-of-range
    extras = [r for r in ranked if r["green"] and r not in picks][:TOP_N - len(picks)]
    picks.extend(extras)

print(f"\n=== PICKED {len(picks)} SETUPS ===")
for r in picks:
    print(f"  {r['sym']:6s} ${r['price']:.2f}  +{r['pct']:.1f}%  rvol {r['rvol']:.1f}×")

# 3. Buy 1 share each
print(f"\n=== SUBMITTING BUY ORDERS ({len(picks)}) ===")
buys = {}
for r in picks:
    try:
        # tradable-check
        a = tc.get_asset(r["sym"])
        if not a.tradable:
            print(f"  SKIP {r['sym']} (not tradable)")
            continue
        o = tc.submit_order(MarketOrderRequest(
            symbol=r["sym"], qty=1, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ))
        buys[r["sym"]] = {"order_id": o.id, "submitted_at": time.time()}
        print(f"  BUY {r['sym']:6s} order_id={str(o.id)[:8]}")
    except Exception as e:
        print(f"  FAIL {r['sym']}: {e}")

# 4. Wait for fills
print(f"\n=== WAITING FOR FILLS ({len(buys)}) ===")
fills = {}
for _ in range(20):
    time.sleep(1)
    pending = [s for s in buys if s not in fills]
    if not pending:
        break
    for sym in pending:
        try:
            o = tc.get_order_by_id(buys[sym]["order_id"])
            if str(o.status) in ("OrderStatus.FILLED", "filled"):
                fills[sym] = float(o.filled_avg_price)
                print(f"  ✓ {sym} filled @ ${fills[sym]:.4f}")
        except Exception as e:
            print(f"  err {sym}: {e}")

# 5. Hold
print(f"\n=== HOLDING {HOLD_SECONDS}s ===")
time.sleep(HOLD_SECONDS)

# 6. Close all
print(f"\n=== CLOSING ALL POSITIONS ===")
try:
    tc.close_all_positions(cancel_orders=True)
except Exception as e:
    print(f"close-all failed: {e}")

# Warte auf close-fills + sammle filled-sell-prices
time.sleep(5)
sells = {}
try:
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    recent = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED, limit=50))
    for o in recent:
        if o.symbol in fills and str(o.side) == "OrderSide.SELL" and o.filled_avg_price:
            sells[o.symbol] = float(o.filled_avg_price)
except Exception as e:
    print(f"  close-poll failed: {e}")

# 7. PnL summary
print("\n" + "=" * 70)
print("FINAL RESULTS")
print("=" * 70)
total_pnl = 0.0
for sym in fills:
    buy = fills[sym]
    sell = sells.get(sym)
    if sell:
        pnl = sell - buy
        total_pnl += pnl
        sign = "+" if pnl >= 0 else ""
        print(f"  {sym:6s}  BUY ${buy:7.4f} → SELL ${sell:7.4f}  PnL {sign}${pnl:+.4f}")
    else:
        print(f"  {sym:6s}  BUY ${buy:7.4f} → (no sell-fill seen)")
print("-" * 70)
print(f"  TOTAL PnL: ${total_pnl:+.4f}  on {len(fills)} trades")
print(f"  Win-Rate:  {sum(1 for s in fills if sells.get(s,buy)>fills[s])}/{len(fills)}")
print("=" * 70)
