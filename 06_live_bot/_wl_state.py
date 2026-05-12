"""Live-State der aktuellen Watchlist-Symbole."""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_alpaca_keys
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from datetime import datetime

WL = ["CLIK", "EEX", "FGMCU", "GAMB", "GSIT", "HSPT", "INBS", "ODYS", "WOK", "XGN"]
K, S = get_alpaca_keys()
dc = StockHistoricalDataClient(K, S)

snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=WL))

print(f"=== WATCHLIST-STATE @ {datetime.now():%H:%M:%S} CET ===")
print(f"{'Sym':6s} {'Last':>9s} {'Open':>9s} {'High':>9s} {'Low':>9s} {'Prev':>9s} {'Δ%':>8s} {'Vol':>14s} {'RVOL':>7s}")
rows = []
for sym in WL:
    sn = snaps.get(sym)
    if not sn:
        print(f"{sym:6s}  (no data)")
        continue
    b = sn.daily_bar; p = sn.previous_daily_bar; t = sn.latest_trade
    if not (b and p and t):
        print(f"{sym:6s}  incomplete")
        continue
    last = t.price
    pct = (last - p.close) / p.close * 100 if p.close else 0
    rvol = b.volume / max(p.volume, 1) if p.volume else 0
    rows.append((sym, last, b.open, b.high, b.low, p.close, pct, b.volume, rvol))

# nach pct sortiert (best zuerst)
rows.sort(key=lambda r: -r[6])
for sym, last, op, hi, lo, prev, pct, vol, rvol in rows:
    flag = "🟢" if pct > 0 else "🔴"
    print(f"{flag}{sym:5s} ${last:>8.2f} ${op:>8.2f} ${hi:>8.2f} ${lo:>8.2f} ${prev:>8.2f} {pct:>+7.1f}% {vol:>14,} {rvol:>6.2f}×")
