"""1-Share-Micro-Test: prove der Order-Pipeline live geht.

Logik:
  1. Snapshot der heutigen Watchlist über Alpaca
  2. Wähle den Kandidaten mit: tradable=True, Preis $2-20, intraday > +5%,
     letzter Bar grün, höchstes (rvol * pct). Fallback: SPY (immer liquide).
  3. Market-Buy 1 Share
  4. Warte 5s → Market-Sell-To-Close
  5. Print PnL
"""
from __future__ import annotations
import os, sys, time, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from secrets_loader import get_alpaca_keys
from watchlist_persist import load_watchlist_if_fresh
KEY, SEC = get_alpaca_keys()

# Audit-Iter 32 (Bug MT-3): heutige Watchlist von Disk laden statt hardcoded.
# Fallback auf liquide Cameron-Klassiker wenn no fresh watchlist.
HARDCODED_FALLBACK = ["TRAW", "WTF", "WEST", "ANPA", "STFS", "CODX", "MASK", "RXT"]
CANDIDATES = load_watchlist_if_fresh() or HARDCODED_FALLBACK
FALLBACK = "SPY"  # immer tradable, immer liquide


def _status_is(status, target: str) -> bool:
    """Audit-Iter 32 (Bug MT-1): tolerant gegen alpaca-py Enum-Repr-Drift.
    Mirror von safe_bracket.py's _status_is helper."""
    if status is None:
        return False
    for accessor in (
        getattr(status, "value", None),
        getattr(status, "name", None),
        str(status),
        str(status).rsplit(".", 1)[-1] if "." in str(status) else None,
    ):
        if accessor is None:
            continue
        if str(accessor).strip().upper() == target.upper():
            return True
    return False

tc = TradingClient(KEY, SEC, paper=True)
dc = StockHistoricalDataClient(KEY, SEC)

print("=" * 60)
print("MICRO-TEST-TRADE  —  1 Share, prove pipeline works")
print("=" * 60)

# 1. Snapshot
snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=CANDIDATES))
ranked = []
for sym, snap in snaps.items():
    try:
        b = snap.daily_bar
        p = snap.previous_daily_bar
        last = snap.latest_trade
        if not (b and p and last):
            continue
        price = last.price
        pct = (price - p.close) / p.close * 100
        rvol = b.volume / max(p.volume, 1)
        # bullish bar?
        green = b.close >= b.open
        if not (2 <= price <= 20):
            continue
        ranked.append((sym, price, pct, rvol, green))
    except Exception as e:
        print(f"  {sym} snapshot-err: {e}")

ranked.sort(key=lambda r: -(r[3] * max(r[2], 0.1)))
print("\nRanking:")
for sym, price, pct, rvol, green in ranked[:8]:
    print(f"  {sym:6s} ${price:6.2f}  pct={pct:+5.1f}%  rvol={rvol:5.1f}  green={green}")

# Pick best green positive pct, fallback SPY
pick = None
for sym, price, pct, rvol, green in ranked:
    if green and pct > 0:
        # Asset-Check
        try:
            asset = tc.get_asset(sym)
            if asset.tradable and asset.fractionable is not None:
                pick = (sym, price)
                break
        except Exception:
            continue

if not pick:
    print("\nKein grüner Kandidat → Fallback SPY")
    last = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=[FALLBACK]))[FALLBACK].latest_trade.price
    pick = (FALLBACK, last)

sym, est_price = pick
print(f"\nPICK: {sym} @ ~${est_price:.2f}")
print(f"Est. cost for 1 share: ${est_price:.2f}  (minimaler Test)")

# 2. Buy 1 share market
order = tc.submit_order(MarketOrderRequest(
    symbol=sym, qty=1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,
))
print(f"\n[BUY] order_id={order.id}  status={order.status}")

# 3. Wait for fill (Audit-Iter 32, Bug MT-1: robust status compare)
filled_buy = None
for i in range(15):
    time.sleep(1)
    o = tc.get_order_by_id(order.id)
    if _status_is(o.status, "FILLED"):
        filled_buy = float(o.filled_avg_price)
        print(f"  filled @ ${filled_buy:.4f} after {i+1}s")
        break
    print(f"  status: {o.status}")
else:
    print("  TIMEOUT — cancel + abort")
    try: tc.cancel_order_by_id(order.id)
    except Exception: pass
    sys.exit(1)

# 4. Hold 5s
print("\nHolding 5s…")
time.sleep(5)

# 5. Sell to close
close_order = tc.close_position(sym)
print(f"\n[SELL] order_id={close_order.id}")
filled_sell = None
for i in range(15):
    time.sleep(1)
    o = tc.get_order_by_id(close_order.id)
    if _status_is(o.status, "FILLED"):
        filled_sell = float(o.filled_avg_price)
        print(f"  filled @ ${filled_sell:.4f} after {i+1}s")
        break
    print(f"  status: {o.status}")
else:
    # Audit-Iter 32 (Bug MT-2): sell timeout ohne cleanup → stranded position.
    # Versuche close_position nochmal, dann manual cancel der pending order.
    print("  SELL-TIMEOUT — position may be stranded! Attempting cleanup…")
    try:
        tc.cancel_order_by_id(close_order.id)
        print(f"    cancelled close order {close_order.id}")
    except Exception as e:
        print(f"    cancel failed: {e}")
    try:
        # Retry close
        tc.close_position(sym)
        print(f"    second close attempt submitted for {sym}")
    except Exception as e:
        print(f"    second close failed: {e}")
    print("  CHECK alpaca dashboard manually for open positions!")

if filled_buy and filled_sell:
    pnl = filled_sell - filled_buy
    print("\n" + "=" * 60)
    print(f"RESULT  {sym}: BUY ${filled_buy:.4f}  →  SELL ${filled_sell:.4f}  PnL ${pnl:+.4f}")
    print("=" * 60)
    print("\n✅ TRADING PIPELINE WORKS — Paper-Account got order through.")
