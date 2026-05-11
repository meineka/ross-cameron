"""Was hätten wir heute mit der 12:27-Watchlist tradet?
Lädt 1m-Daten für die 10 Symbole und prüft simple Bull-Flag-Setups."""
from __future__ import annotations
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import yfinance as yf
import pandas as pd
from datetime import datetime

# Watchlist nach FAST-Rescan @ 12:31 CET (07:31 ET — vor Open)
WATCHLIST = ["TRAW","CODX","MASK","STFS","ANPA","WEST","WTF","RXT","MTEX","TC"]

def analyze(sym: str):
    try:
        t = yf.Ticker(sym)
        df = t.history(period="1d", interval="1m", prepost=False)
        if df.empty or len(df) < 10:
            return f"{sym}: keine Daten"
        op = df["Open"].iloc[0]
        hi = df["High"].max()
        lo = df["Low"].min()
        cl = df["Close"].iloc[-1]
        vol = int(df["Volume"].sum())
        pct_hi = (hi - op) / op * 100
        pct_cl = (cl - op) / op * 100
        # naive Bull-Flag: New-High nach Pullback (max 3 bars red), dann grüner Break
        flags = 0
        green = (df["Close"] > df["Open"]).astype(int)
        for i in range(5, len(df)-1):
            window = df.iloc[i-5:i]
            if window["High"].max() == hi:
                # erst nach high-of-day pullback dann break
                continue
            # local pattern: 2-3 red dann break über prior high
            last3 = green.iloc[i-3:i].sum()
            if last3 <= 1 and df["Close"].iloc[i] > df["High"].iloc[i-3:i].max():
                flags += 1
        return f"{sym}: O={op:.2f} H={hi:.2f} L={lo:.2f} C={cl:.2f}  +{pct_hi:.1f}% high / {pct_cl:+.1f}% close  vol={vol:,}  BF-Setups={flags}"
    except Exception as e:
        return f"{sym}: ERROR {e}"

print(f"=== TODAY-ANALYSIS @ {datetime.now():%H:%M} ===")
for s in WATCHLIST:
    print(" ", analyze(s))
