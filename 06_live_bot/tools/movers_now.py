"""Live-Movers-Refresh: top Cameron-Setups jetzt via Alpaca-Snapshot."""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from secrets_loader import get_alpaca_keys
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

# Broader Universe — heutige WL + Cameron-Klassiker + Pre-Market-Movers
UNIVERSE = [
    "WOK","ODYS","HSPT","CLIK","XGN","GSIT","INBS","GAMB","EEX","FGMCU",
    "TRAW","STFS","MTEX","WEST","ANPA","CODX","MASK","RXT","TC","WTF",
    "MARA","RIOT","CLSK","BITF","HUT","HIVE","CIFR","BTBT","WULF","CAN",
    "SOFI","RKT","PLTR","OPEN","NU","ROOT","SOUN",
    "BLNK","CHPT","NIO","XPEV","LCID","RIVN",
    "SAVA","ATAI","DNN","UEC","ENPH","SE","FFIE","NKLA","HOLO",
    "MULN","BBBY","AMC","GME","PROG","ATER","BBIG","IMPP",
    "TLRY","SNDL","ACB",
    "MEX","HPAI","CNSP","ATRA","ENRA","PMAX","ELMT","LABT",
    "AAPL","MSFT","NVDA","TSLA","AMD","META","AMZN","GOOGL",
]

K, S = get_alpaca_keys()
dc = StockHistoricalDataClient(K, S)

snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=UNIVERSE))
rows = []
for sym, sn in snaps.items():
    try:
        b, p, t = sn.daily_bar, sn.previous_daily_bar, sn.latest_trade
        if not (b and p and t and p.close > 0 and p.volume > 0): continue
        price = t.price
        pct = (price - p.close) / p.close * 100
        rvol = b.volume / max(p.volume, 1)
        green = b.close >= b.open
        score = rvol * max(pct, 0.1)
        rows.append((sym, price, pct, rvol, green, score))
    except Exception:
        continue

# Cameron-strict: $2-$20, green, pct>0
strict = [r for r in rows if 2 <= r[1] <= 20 and r[4] and r[2] > 0]
strict.sort(key=lambda r: -r[5])

# All other (rejected) for context
others = [r for r in rows if not (2 <= r[1] <= 20 and r[4] and r[2] > 0)]
others.sort(key=lambda r: -abs(r[2]))

print("=" * 78)
print(f"LIVE MOVERS @ {__import__('datetime').datetime.now():%H:%M}  ({len(rows)} symbols scanned)")
print("=" * 78)
print("\n🟢 CAMERON-STRICT CANDIDATES ($2-$20, green, +pct):")
print(f"  {'Sym':6s} {'Price':>9s} {'Δ%':>7s} {'RVOL':>7s}  score")
for sym, price, pct, rvol, green, score in strict[:15]:
    print(f"  {sym:6s} ${price:>8.2f} {pct:>+6.1f}% {rvol:>6.1f}× {score:>7.0f}")

print(f"\n⚪ TOP-MOVERS REJECTED (out of range/red/negative) — first 15:")
print(f"  {'Sym':6s} {'Price':>9s} {'Δ%':>7s} {'RVOL':>7s}  reason")
for sym, price, pct, rvol, green, score in others[:15]:
    reasons = []
    if not (2 <= price <= 20): reasons.append(f"price ${price:.2f}")
    if not green: reasons.append("red")
    if pct <= 0: reasons.append(f"{pct:+.1f}%")
    print(f"  {sym:6s} ${price:>8.2f} {pct:>+6.1f}% {rvol:>6.1f}×  {', '.join(reasons)}")
