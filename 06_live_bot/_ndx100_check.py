"""Check: passt Cameron's 5-Pillars-Filter auf NASDAQ-100?"""
from __future__ import annotations
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from secrets_loader import get_alpaca_keys
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest

# NASDAQ-100 (gängige Liste, ~100 Names)
NDX100 = [
    "AAPL","MSFT","NVDA","GOOGL","GOOG","META","AMZN","TSLA","AVGO","PEP",
    "COST","ADBE","NFLX","AMD","CSCO","INTC","TMUS","CMCSA","TXN","QCOM",
    "INTU","HON","AMGN","AMAT","ISRG","BKNG","SBUX","VRTX","ADP","MDLZ",
    "GILD","ADI","REGN","LRCX","PANW","KLAC","SNPS","CDNS","MU","MELI",
    "ASML","CSX","ABNB","MAR","CRWD","CTAS","PYPL","FTNT","ORLY","WDAY",
    "PCAR","NXPI","ROP","CHTR","MNST","ADSK","KDP","CPRT","AEP","PAYX",
    "ODFL","FAST","ROST","MRVL","DXCM","BKR","DDOG","KHC","EXC","XEL",
    "VRSK","CTSH","CSGP","TEAM","IDXX","CCEP","FANG","EA","ZS","ANSS",
    "ON","BIIB","CDW","GFS","TTWO","WBD","DLTR","MDB","SIRI","ILMN",
    "WBA","ENPH","LULU","ARM","PDD","SMCI","MRNA","CEG","LIN","TTD",
]

K, S = get_alpaca_keys()
dc = StockHistoricalDataClient(K, S)

print("=" * 78)
print(f"CAMERON-FILTER auf NASDAQ-100 ({len(NDX100)} Namen)")
print("=" * 78)

snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=NDX100))
rows = []
for sym, sn in snaps.items():
    try:
        b, p, t = sn.daily_bar, sn.previous_daily_bar, sn.latest_trade
        if not (b and p and t and p.close > 0 and p.volume > 0):
            continue
        price = t.price
        pct = (price - p.close) / p.close * 100
        rvol = b.volume / max(p.volume, 1)
        rows.append((sym, price, pct, rvol))
    except Exception:
        continue

# 5 Pillars
p1 = sum(1 for s, p, _, _ in rows if 2 <= p <= 20)
p3 = sum(1 for s, _, _, r in rows if r >= 5.0)
p4 = sum(1 for s, _, pct, _ in rows if pct >= 10.0)
p1p3p4 = sum(1 for s, p, pct, r in rows if 2 <= p <= 20 and r >= 5.0 and pct >= 10.0)

print(f"\nGesamt mit Daten: {len(rows)}")
print()
print("PILLAR-MATCHES:")
print(f"  Preis $2-$20:        {p1} / {len(rows)}")
print(f"  RVOL >= 5×:          {p3} / {len(rows)}")
print(f"  Daily +10% min:      {p4} / {len(rows)}")
print(f"  ALL 3 zusammen:      {p1p3p4} / {len(rows)}")

# Top 10 nach Cameron-Score
rows.sort(key=lambda x: -(x[3] * max(x[2], 0.1)))
print("\nTOP-10 nach Cameron-Score (rvol × pct):")
print(f"  {'Sym':6s} {'Price':>10s} {'Δ%':>7s} {'RVOL':>7s}  | Pillar-Check")
for sym, price, pct, rvol in rows[:10]:
    pcheck = []
    pcheck.append("$" if 2 <= price <= 20 else "·")
    pcheck.append("R" if rvol >= 5.0 else "·")
    pcheck.append("%" if pct >= 10.0 else "·")
    print(f"  {sym:6s} ${price:>9.2f} {pct:>+6.1f}% {rvol:>6.1f}×  | {''.join(pcheck)}")

# Volatility-Stats für realistische Strategie-Sicht
print("\nVOLATILITÄTS-STATS (Tagesbewegungen heute):")
absp = [abs(x[2]) for x in rows]
absp.sort(reverse=True)
print(f"  Größter Mover heute: {rows[0][0]} {rows[0][2]:+.1f}%")
maxp = max(absp) if absp else 0
print(f"  Top10 ø |Δ%|:        {sum(absp[:10])/10:.2f}%")
print(f"  Top50 ø |Δ%|:        {sum(absp[:50])/50:.2f}%")
print(f"  Σ Names mit |Δ%|>5%: {sum(1 for x in absp if x > 5)}")
print(f"  Σ Names mit |Δ%|>10%:{sum(1 for x in absp if x > 10)}")
print("=" * 78)
