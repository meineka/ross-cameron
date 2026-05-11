"""Cameron-Live-Demo: echte Bull-Flag-Detection auf 10 Mid-Caps,
mit T1/T2/Stop/MACD-Exit, Limit-Orders, max 2 share/trade.

Demo-Modus: TIME_NEW_ENTRIES_END auf "jetzt + 90 min" gesetzt damit
das Script in non-Cameron-Time arbeiten kann. Alle anderen Filter strict.
"""
from __future__ import annotations
import os, sys, time, io, asyncio
from pathlib import Path
from datetime import datetime, timedelta, time as dtime, timezone
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed

from secrets_loader import get_alpaca_keys
from indicators import macd_is_bullish, macd_bear_cross, false_breakout_veto
from vwap_filter import is_above_vwap

# ─── Config ──────────────────────────────────────────────────────────────────
DEMO_DURATION_MIN = 60        # 60 min Watch-Window
SHARES_PER_TRADE = 2          # demo: 2 shares (~ $4-40 risk)
QUICK_EXIT_CENTS = 0.30
HOLD_MIN_BARS = 1
PRICE_MIN, PRICE_MAX = 2.0, 20.0

UNIVERSE = [
    "TRAW", "WTF", "WEST", "ANPA", "STFS", "CODX", "MASK", "RXT", "MTEX", "TC",
    "MARA", "RIOT", "CLSK", "BITF", "SOFI", "OPEN", "AMC",
    "MULN", "BBBY", "PLTR", "RKT",
]

# ─── State ───────────────────────────────────────────────────────────────────
KEY, SEC = get_alpaca_keys()
tc = TradingClient(KEY, SEC, paper=True)
dc = StockHistoricalDataClient(KEY, SEC)

bars_per_symbol: dict[str, list[dict]] = {}
positions: dict[str, dict] = {}   # symbol -> {entry, stop, t1, t2, shares, entry_ts, half_filled}
trades_log: list[dict] = []
demo_end_ts = time.time() + DEMO_DURATION_MIN * 60


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ─── 1. Pick Top-10 Cameron-strict ───────────────────────────────────────────
def pick_watchlist() -> list[str]:
    snaps = dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=UNIVERSE))
    ranked = []
    for sym, snap in snaps.items():
        try:
            b, p, t = snap.daily_bar, snap.previous_daily_bar, snap.latest_trade
            if not (b and p and t and p.close > 0 and p.volume > 0):
                continue
            price = t.price
            pct = (price - p.close) / p.close * 100
            rvol = b.volume / max(p.volume, 1)
            green = b.close >= b.open
            in_range = PRICE_MIN <= price <= PRICE_MAX
            if not (in_range and green and pct > 0):
                continue
            ranked.append((sym, price, pct, rvol, rvol * max(pct, 0.1)))
        except Exception:
            continue
    ranked.sort(key=lambda r: -r[4])
    log(f"WATCHLIST candidates ($2-$20, green, pct>0): {len(ranked)}")
    for sym, price, pct, rvol, score in ranked[:10]:
        log(f"  {sym:6s} ${price:6.2f}  +{pct:4.1f}%  rvol {rvol:4.1f}×  score={score:.0f}")
    return [r[0] for r in ranked[:10]]


# ─── 2. Bootstrap historische 5-Min-Bars (für Pattern-Detection) ─────────────
def bootstrap_history(symbols: list[str]):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=8)
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=TimeFrame.Minute,
            start=start, end=end,
        )
        data = dc.get_stock_bars(req).data
        for sym in symbols:
            bars_per_symbol[sym] = []
            if sym not in data:
                continue
            # 1-Min → 5-Min resampeln
            mins = data[sym]
            buf = []
            for m in mins:
                buf.append({
                    "open": float(m.open), "high": float(m.high),
                    "low": float(m.low), "close": float(m.close),
                    "volume": int(m.volume), "ts": m.timestamp,
                })
            # einfaches 5er-Bucket
            for i in range(0, len(buf), 5):
                grp = buf[i:i+5]
                if not grp:
                    continue
                bars_per_symbol[sym].append({
                    "open": grp[0]["open"],
                    "high": max(x["high"] for x in grp),
                    "low":  min(x["low"]  for x in grp),
                    "close": grp[-1]["close"],
                    "volume": sum(x["volume"] for x in grp),
                    "ts": grp[-1]["ts"],
                })
            log(f"  {sym} bootstrap: {len(bars_per_symbol[sym])} 5-min bars")
    except Exception as e:
        log(f"bootstrap fail: {e}")


# ─── 3. Bull-Flag-Detection (gespiegelt aus bot.py mit allen Vetos) ──────────
import numpy as np
import pandas as pd

POLE_MIN, POLE_MAX = 3, 7
FLAG_MIN, FLAG_MAX = 1, 3
POLE_MIN_MOVE = 5.0          # gelockert von Cameron 5% für 5-min intraday
POLE_TOP_TAIL_MAX = 0.4
FLAG_RETRACE_MAX = 50.0
BREAKOUT_VOL_FACTOR = 1.5


def detect_bull_flag(bars: list[dict]) -> tuple[bool, dict]:
    if len(bars) < 10:
        return False, {}
    o = np.array([b["open"] for b in bars])
    h = np.array([b["high"] for b in bars])
    l = np.array([b["low"]  for b in bars])
    c = np.array([b["close"] for b in bars])
    v = np.array([b["volume"] for b in bars])
    green = c > o
    rng = np.maximum(h - l, 1e-9)
    topping = (h - np.maximum(c, o)) / rng
    vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()
    i = len(bars) - 1
    if not green[i]: return False, {"_v": "red_breakout"}
    if c[i] < PRICE_MIN or c[i] > PRICE_MAX: return False, {"_v": "price"}
    if np.isnan(vol_sma[i]) or v[i] < vol_sma[i] * BREAKOUT_VOL_FACTOR:
        return False, {"_v": "vol"}
    for fl in range(FLAG_MIN, FLAG_MAX + 1):
        for pl in range(POLE_MIN, POLE_MAX + 1):
            ps = i - fl - pl; pe = i - fl
            if ps < 0: continue
            if not green[ps:pe].all(): continue
            p_start, p_end = o[ps], c[pe-1]
            if p_start <= 0: continue
            p_pct = (p_end - p_start) / p_start * 100
            if p_pct < POLE_MIN_MOVE: continue
            if topping[ps:pe].max() > POLE_TOP_TAIL_MAX: continue
            p_h = p_end - p_start
            if p_h <= 0: continue
            fs, fe = pe, i
            fl_low = l[fs:fe].min()
            if (p_end - fl_low) / p_h * 100 > FLAG_RETRACE_MAX: continue
            prh = h[fs:fe].max()
            if h[i] <= prh: continue
            ep = prh + 0.02
            sp = fl_low - 0.02
            if ep <= sp: continue
            # Vetos
            if not is_above_vwap(bars, c[i]):
                return False, {"_v": "vwap"}
            if not macd_is_bullish(c.tolist()):
                return False, {"_v": "macd"}
            vetoed, why = false_breakout_veto(bars)
            if vetoed:
                return False, {"_v": f"fbo_{why}"}
            return True, {
                "entry": float(ep), "stop": float(sp),
                "t1": float(ep + (ep - sp)),
                "t2": float(ep + p_h),
                "pole_height": float(p_h),
            }
    return False, {}


# ─── 4. WS-Stream + Pattern-Loop ─────────────────────────────────────────────
async def run_demo(watchlist: list[str]):
    # Pre-positions (von vorher)
    try:
        existing = tc.get_all_positions()
        if existing:
            log(f"Cleaning {len(existing)} existing positions before demo")
            tc.close_all_positions(cancel_orders=True)
            await asyncio.sleep(3)
    except Exception as e:
        log(f"pre-clean fail: {e}")

    log(f"Starting WS-stream on {len(watchlist)} symbols, duration {DEMO_DURATION_MIN}min")
    ws = StockDataStream(KEY, SEC, feed=DataFeed.IEX)

    async def on_bar(bar):
        sym = bar.symbol
        b = {"open": bar.open, "high": bar.high, "low": bar.low,
             "close": bar.close, "volume": bar.volume, "ts": bar.timestamp}
        bars_per_symbol.setdefault(sym, []).append(b)
        log(f"  bar {sym}: O={b['open']:.2f} H={b['high']:.2f} L={b['low']:.2f} C={b['close']:.2f} V={b['volume']}")

        # 1) Manage existing position
        # Stop + Take-Profit (T2) liegen als BRACKET broker-seitig — Alpaca
        # triggert die selber. Script macht nur MACD-Bear-Cross-Exit als Bonus.
        if sym in positions:
            closes = [x["close"] for x in bars_per_symbol[sym]]
            if len(closes) >= 30 and macd_bear_cross(closes):
                await close_position(sym, b["close"], reason="macd_bear_cross")
                return
            # Check ob Bracket bereits gefilled hat
            try:
                live = tc.get_all_positions()
                if not any(p.symbol == sym for p in live):
                    log(f"  ⚑ BRACKET {sym} closed by broker — removing tracker")
                    positions.pop(sym, None)
            except Exception:
                pass
            return

        # 2) Detect new pattern
        if sym not in watchlist:
            return
        ok, params = detect_bull_flag(bars_per_symbol[sym])
        if not ok:
            return
        log(f"  ⚡ BULL-FLAG {sym}  entry={params['entry']:.2f} stop={params['stop']:.2f} t1={params['t1']:.2f} t2={params['t2']:.2f}")
        await enter_position(sym, params)

    ws.subscribe_bars(on_bar, *watchlist)

    # Run WS in thread
    ws_task = asyncio.create_task(asyncio.to_thread(ws.run))

    # Watchdog: stop after duration or when market close near
    while time.time() < demo_end_ts:
        await asyncio.sleep(5)
    log("Demo-Zeit abgelaufen — flatten alle Positions")
    try:
        ws.stop_ws()
    except Exception:
        pass
    try:
        tc.close_all_positions(cancel_orders=True)
    except Exception:
        pass


async def enter_position(sym: str, params: dict):
    """BRACKET-Order: Entry-Limit + Stop-Loss + Take-Profit (T2) gleichzeitig.
    Alle drei Orders live bei Alpaca — schützt auch wenn Script crasht."""
    try:
        o = tc.submit_order(LimitOrderRequest(
            symbol=sym, qty=SHARES_PER_TRADE, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(params["entry"] + 0.02, 2),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(params["t2"], 2)),
            stop_loss=StopLossRequest(stop_price=round(params["stop"], 2)),
        ))
        positions[sym] = {**params, "shares": SHARES_PER_TRADE,
                          "entry_ts": time.time(), "half_filled": False,
                          "order_id": o.id}
        trades_log.append({"ts": time.time(), "sym": sym, "side": "BUY",
                           "qty": SHARES_PER_TRADE, "price": params["entry"]})
        log(f"  → BRACKET-BUY {SHARES_PER_TRADE} {sym}  entry~${params['entry']:.2f}  "
            f"STOP=${params['stop']:.2f}  TP=${params['t2']:.2f}")
    except Exception as e:
        log(f"  BUY fail {sym}: {e}")


async def partial_close(sym: str, qty: int, price: float, reason: str):
    try:
        tc.submit_order(LimitOrderRequest(
            symbol=sym, qty=qty, side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(price - 0.02, 2),
        ))
        trades_log.append({"ts": time.time(), "sym": sym, "side": "SELL",
                           "qty": qty, "price": price, "reason": reason})
        log(f"  → SELL-T1 {qty} {sym} @ ~${price:.2f} ({reason})")
    except Exception as e:
        log(f"  SELL-T1 fail {sym}: {e}")


async def close_position(sym: str, price: float, reason: str):
    if sym not in positions: return
    pos = positions[sym]
    try:
        # Bracket-Children canceln, dann Position schließen
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            open_orders = tc.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[sym], limit=20,
            ))
            for o in open_orders:
                try:
                    tc.cancel_order_by_id(o.id)
                except Exception:
                    pass
        except Exception:
            pass
        tc.close_position(sym)
        trades_log.append({"ts": time.time(), "sym": sym, "side": "SELL",
                           "qty": pos["shares"], "price": price, "reason": reason})
        log(f"  → CLOSE {sym} ({reason}) @ ~${price:.2f}")
    except Exception as e:
        log(f"  CLOSE fail {sym}: {e}")
    finally:
        positions.pop(sym, None)


# ─── 5. Main ─────────────────────────────────────────────────────────────────
async def main():
    log("=" * 60)
    log("CAMERON-LIVE-DEMO (90 min, real bull-flag detection)")
    log("=" * 60)
    wl = pick_watchlist()
    if not wl:
        log("Keine Cameron-Setups in Universe — abort")
        return
    bootstrap_history(wl)
    await run_demo(wl)

    # Final report
    log("=" * 60)
    log("DEMO ENDE")
    log("=" * 60)
    log(f"Trades logged: {len(trades_log)}")
    for t in trades_log:
        log(f"  {datetime.fromtimestamp(t['ts']):%H:%M:%S}  {t['side']:4s} {t['qty']} {t['sym']:6s} @ ${t['price']:.2f}  {t.get('reason','')}")
    try:
        acc = tc.get_account()
        log(f"Account-Equity nach Demo: ${float(acc.equity):,.2f}")
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
