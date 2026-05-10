"""
bot.py — Cameron-Style Live-Trading-Bot (Alpaca Paper).

Pipeline:
  1) Premarket Top-10 Scanner (yfinance, 12:30 CET / 06:30 ET täglich)
  2) Alpaca Live-Bar-Stream für Top-10 Tickers
  3) Bull-Flag Pattern-Detector pro neuem Bar
  4) Paper-Order via Alpaca (Limit + Offset)
  5) Position-Management: T1/T2/Stop/MACD-Exit
  6) Per-Rank-Log (validiert Rank 2-7 Sweet-Spot live)
  7) Hard-Caps: Daily-Max-Loss, Position-Sizing, Spiral-Detection

Setup vor Run:
  1) Alpaca-Paper-Account → API-Keys
  2) export APCA_API_KEY_ID="..."
  3) export APCA_API_SECRET_KEY="..."
  4) python bot.py --dry-run                # nur Pattern-Detection, kein Order
  5) python bot.py                          # Paper-Trading live
  6) python bot.py --replay 2024-09-13      # Historical-Day für Test

Verwendet:
  - yfinance: Pre-Market-Scanner + Daily-Bars für RVOL-Filter
  - alpaca-py: Live-Bar-Stream + Paper-Trading-API
  - constraints.yaml: alle Cameron-Regeln referenziert
"""
from __future__ import annotations
import sys, io, os, asyncio, logging, argparse, json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from datetime import datetime, timedelta, timezone, time as dtime
from collections import deque

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import yfinance as yf

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.data.live import StockDataStream
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot")

# ─── Cameron-Constants (mirror constraints.yaml) ────────────────────────────
PRICE_MIN, PRICE_MAX = 2.0, 20.0
DAILY_GAIN_MIN_PCT = 10.0
RVOL_MIN_PROXY = 2.0
TOP_N = 10
TIMEFRAME = "5Min"

POLE_MIN_CANDLES, POLE_MAX_CANDLES = 3, 7
POLE_MIN_MOVE_PCT = 5.0
POLE_TOPPING_TAIL_MAX = 0.4
FLAG_MIN_CANDLES, FLAG_MAX_CANDLES = 1, 3
FLAG_RETRACE_MAX_PCT = 50.0
BREAKOUT_VOL_FACTOR = 1.5
SLIPPAGE_CENTS = 0.01

MAX_LOSS_PER_TRADE_USD = 50.0      # Paper-Modus konservativ
DAILY_MAX_LOSS_USD = 150.0          # = 3× max-loss-per-trade
DAILY_GOAL_USD = 150.0              # symmetric (Cameron-Rule)
INTRADAY_DRAWDOWN_PCT_OF_PROFITS = 50.0

LIQUIDITY_CAP_PCT_OF_AVG_VOL = 1.0  # max 1% von 1-min-avg-volume
QUARTER_SIZE_UNLOCK_CENTS = 0.20    # nach +20¢/Aktie kumuliert: full size

# Time-Cuts (NY-Time)
TIME_NEW_ENTRIES_END = dtime(11, 30)
TIME_HARD_FLAT = dtime(12, 0)
TIME_RTH_START = dtime(9, 30)

DATA_DIR = Path(__file__).parent

# ─── Datenklassen ───────────────────────────────────────────────────────────
@dataclass
class TickerState:
    symbol: str
    rank: int = 0           # 1..N from premarket scanner
    score: float = 0.0
    bars: deque = field(default_factory=lambda: deque(maxlen=80))
    pullback_count_today: int = 0   # for 3rd-pullback rule
    in_position: bool = False
    entry_price: float = 0.0
    stop_price: float = 0.0
    target1_price: float = 0.0
    target2_price: float = 0.0
    half_filled: bool = False
    shares: int = 0
    pole_candles: int = 0
    flag_candles: int = 0


@dataclass
class DayState:
    date: str = ""
    realized_pnl: float = 0.0
    peak_pnl: float = 0.0
    consecutive_losses: int = 0
    quarter_size_unlocked: bool = False
    cents_per_share_cumulative: float = 0.0
    spiral_locked: bool = False


# ─── Premarket-Scanner ──────────────────────────────────────────────────────
def fetch_us_universe() -> list[str]:
    """yfinance/NASDAQ-Trader: alle US-Tickers."""
    import requests, io as _io
    urls = [
        "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
    ]
    tickers: set[str] = set()
    for u in urls:
        try:
            r = requests.get(u, timeout=20)
            df = pd.read_csv(_io.StringIO(r.text), sep="|")
            col = "Symbol" if "Symbol" in df.columns else "ACT Symbol"
            df = df[df.get("Test Issue", "N") == "N"]
            if "ETF" in df.columns:
                df = df[df["ETF"] == "N"]
            tickers.update(df[col].dropna().astype(str).tolist())
        except Exception as e:
            log.warning("universe fetch fail %s: %s", u, e)
    tickers = {t for t in tickers if t.isalpha() and 1 <= len(t) <= 5}
    return sorted(tickers)


def premarket_scan(top_n: int = TOP_N) -> list[TickerState]:
    """5-Pillars-Filter + Top-N-Composite-Score-Ranking."""
    log.info("Premarket scan: pulling daily bars for filter…")
    tickers = fetch_us_universe()
    log.info("  %d tickers in universe", len(tickers))

    # Daily-Bars 30 Tage → RVOL-Proxy + intraday_pct
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=45)
    cands = []
    batch_size = 200
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        try:
            df = yf.download(
                tickers=batch, start=start.isoformat(), end=end.isoformat(),
                interval="1d", group_by="ticker", auto_adjust=False,
                progress=False, threads=True,
            )
        except Exception:
            continue
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=0, future_stack=True).rename_axis(["date","ticker"]).reset_index()
        else:
            df = df.reset_index(); df["ticker"] = batch[0]
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        df = df.dropna(subset=["close","open","volume"])
        df = df.sort_values(["ticker","date"])
        df["prev_close"] = df.groupby("ticker")["close"].shift(1)
        df["intraday_pct"] = (df["high"] - df["prev_close"]) / df["prev_close"] * 100
        df["avg_vol_20"] = df.groupby("ticker")["volume"].transform(lambda s: s.rolling(20, min_periods=5).mean())
        df["rvol_proxy"] = df["volume"] / df["avg_vol_20"]
        latest = df.groupby("ticker").tail(1)
        latest = latest[
            (latest["close"].between(PRICE_MIN, PRICE_MAX))
            & (latest["intraday_pct"] >= DAILY_GAIN_MIN_PCT)
            & (latest["rvol_proxy"] >= RVOL_MIN_PROXY)
        ]
        cands.append(latest)
    if not cands:
        return []
    all_cands = pd.concat(cands, ignore_index=True)
    all_cands["score"] = all_cands["rvol_proxy"] * all_cands["intraday_pct"]
    all_cands = all_cands.sort_values("score", ascending=False).head(top_n)
    log.info("  Top-%d candidates: %s", top_n, all_cands["ticker"].tolist())
    return [
        TickerState(symbol=row.ticker, rank=int(rank+1), score=float(row.score))
        for rank, row in enumerate(all_cands.itertuples())
    ]


# ─── Pattern-Detector (auf rolling Bar-Window) ──────────────────────────────
def detect_bull_flag(bars: list) -> tuple[bool, dict]:
    """Returns (signal, params). Auf den letzten Bar als potenzielle Breakout-Kerze."""
    if len(bars) < POLE_MIN_CANDLES + FLAG_MIN_CANDLES + 5:
        return False, {}
    o = np.array([b["open"] for b in bars])
    h = np.array([b["high"] for b in bars])
    l = np.array([b["low"] for b in bars])
    c = np.array([b["close"] for b in bars])
    v = np.array([b["volume"] for b in bars])
    green = c > o
    rng = np.maximum(h - l, 1e-9)
    upper_wick = h - np.maximum(c, o)
    topping = upper_wick / rng
    vol_sma = pd.Series(v).rolling(20, min_periods=5).mean().to_numpy()

    i = len(bars) - 1   # candidate breakout = letzte Kerze
    if not green[i]:
        return False, {}
    # Price-Range-Check (Cameron-Veto: Preis $2-$20)
    if c[i] < PRICE_MIN or c[i] > PRICE_MAX:
        return False, {}
    if np.isnan(vol_sma[i]) or v[i] < vol_sma[i] * BREAKOUT_VOL_FACTOR:
        return False, {}

    for fl in range(FLAG_MIN_CANDLES, FLAG_MAX_CANDLES + 1):
        for pl in range(POLE_MIN_CANDLES, POLE_MAX_CANDLES + 1):
            ps = i - fl - pl; pe = i - fl
            if ps < 0: continue
            if not green[ps:pe].all(): continue
            p_start = o[ps]; p_end = c[pe-1]
            if p_start <= 0: continue
            p_pct = (p_end - p_start) / p_start * 100
            if p_pct < POLE_MIN_MOVE_PCT: continue
            if topping[ps:pe].max() > POLE_TOPPING_TAIL_MAX: continue
            fs = pe; fe = i
            p_h = p_end - p_start
            if p_h <= 0: continue
            fl_low = l[fs:fe].min()
            if (p_end - fl_low) / p_h * 100 > FLAG_RETRACE_MAX_PCT: continue
            prh = h[fs:fe].max()
            if h[i] <= prh: continue
            ep = prh + SLIPPAGE_CENTS
            sp = fl_low - SLIPPAGE_CENTS
            if ep <= sp: continue
            return True, {
                "entry_price": float(ep),
                "stop_price": float(sp),
                "target1": float(ep + (ep - sp)),
                "target2": float(ep + p_h),
                "pole_height": float(p_h),
                "pole_candles": int(pl),
                "flag_candles": int(fl),
            }
    return False, {}


# ─── Risk-Engine ────────────────────────────────────────────────────────────
def compute_position_size(entry: float, stop: float, account_equity: float, day: DayState) -> int:
    if entry <= stop: return 0
    risk_per_share = entry - stop
    max_shares = int(MAX_LOSS_PER_TRADE_USD / risk_per_share)
    # Quarter-Size-Rule
    if not day.quarter_size_unlocked:
        max_shares = max_shares // 4
    return max(0, max_shares)


def can_enter_new(day: DayState, ny_time: dtime) -> tuple[bool, str]:
    if day.spiral_locked: return False, "spiral_locked"
    if day.realized_pnl <= -DAILY_MAX_LOSS_USD: return False, "daily_max_loss"
    if day.peak_pnl > 0 and day.realized_pnl < day.peak_pnl * (1 - INTRADAY_DRAWDOWN_PCT_OF_PROFITS/100):
        return False, "intraday_drawdown_50pct"
    if ny_time >= TIME_NEW_ENTRIES_END: return False, "after_1130"
    if ny_time < TIME_RTH_START: return False, "before_rth"
    return True, ""


# ─── Trade-Logger ───────────────────────────────────────────────────────────
class TradeLogger:
    def __init__(self):
        self.path = DATA_DIR / "trades_live.jsonl"

    def log(self, event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")


# ─── Alpaca-Executor ────────────────────────────────────────────────────────
class AlpacaExecutor:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True, dry_run: bool = False):
        self.dry_run = dry_run
        self.client = TradingClient(api_key, api_secret, paper=paper)
        if dry_run:
            log.info("DRY-RUN mode: no orders submitted")

    def get_equity(self) -> float:
        try:
            return float(self.client.get_account().equity)
        except Exception as e:
            log.warning("get_equity err: %s — using $25k default", e)
            return 25000.0

    def submit_buy_limit(self, symbol: str, shares: int, price: float) -> str | None:
        if self.dry_run:
            log.info("[DRY] BUY %s %d @ %.2f", symbol, shares, price)
            return f"dryrun-{symbol}-{datetime.now().timestamp()}"
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
            )
            o = self.client.submit_order(req)
            log.info("BUY %s %d @ %.2f → order_id=%s", symbol, shares, price, o.id)
            return o.id
        except Exception as e:
            log.error("submit_buy err %s: %s", symbol, e)
            return None

    def submit_sell_limit(self, symbol: str, shares: int, price: float, reason: str) -> str | None:
        if self.dry_run:
            log.info("[DRY] SELL %s %d @ %.2f (%s)", symbol, shares, price, reason)
            return f"dryrun-{symbol}-{datetime.now().timestamp()}"
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
            )
            o = self.client.submit_order(req)
            log.info("SELL %s %d @ %.2f → %s", symbol, shares, price, reason)
            return o.id
        except Exception as e:
            log.error("submit_sell err %s: %s", symbol, e)
            return None

    def market_close_all(self):
        if self.dry_run:
            log.info("[DRY] CLOSE ALL"); return
        try:
            self.client.close_all_positions(cancel_orders=True)
            log.info("Closed all positions")
        except Exception as e:
            log.error("close_all err: %s", e)


# ─── Bot Main Loop ──────────────────────────────────────────────────────────
class Bot:
    def __init__(self, api_key: str, api_secret: str, dry_run: bool = False):
        self.executor = AlpacaExecutor(api_key, api_secret, paper=True, dry_run=dry_run)
        self.tickers: dict[str, TickerState] = {}
        self.day = DayState(date=str(datetime.now(timezone.utc).date()))
        self.logger = TradeLogger()
        self.api_key = api_key
        self.api_secret = api_secret

    async def run(self):
        log.info("=" * 60)
        log.info("CAMERON-BOT START — paper trading")
        log.info("=" * 60)
        # 1. Premarket-Scan
        candidates = await asyncio.to_thread(premarket_scan, TOP_N)
        if not candidates:
            log.error("No premarket candidates — abort")
            return
        for ts in candidates:
            self.tickers[ts.symbol] = ts
            self.logger.log({"event": "watchlist", **asdict(ts), "bars": []})

        # 2. Live Bar-Stream Subscribe
        equity = self.executor.get_equity()
        log.info("Account equity: $%.2f", equity)
        log.info("Watching: %s", [t.symbol for t in self.tickers.values()])

        ws = StockDataStream(self.api_key, self.api_secret, feed="iex")  # free tier IEX
        symbols = list(self.tickers.keys())

        async def on_bar(bar):
            await self.handle_bar(bar)

        ws.subscribe_bars(on_bar, *symbols)

        # 3. Time-Cuts-Loop läuft parallel
        async def time_loop():
            while True:
                ny = datetime.now(tz=timezone(timedelta(hours=-4)))  # ET fixed (no DST handling — ok for paper)
                if ny.time() >= TIME_HARD_FLAT:
                    log.info("12:00 ET — hard flat all positions")
                    self.executor.market_close_all()
                    await asyncio.sleep(60)
                    return
                await asyncio.sleep(30)

        # 4. Run WS + Time-Loop concurrently
        try:
            await asyncio.gather(
                asyncio.to_thread(ws.run),
                time_loop(),
            )
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — closing")
            self.executor.market_close_all()

    async def handle_bar(self, bar):
        sym = bar.symbol
        if sym not in self.tickers: return
        ts = self.tickers[sym]
        bar_dict = {
            "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
            "timestamp": bar.timestamp,
        }
        ts.bars.append(bar_dict)
        ny_time = bar.timestamp.astimezone(timezone(timedelta(hours=-4))).time()

        # Manage existing position
        if ts.in_position:
            await self.manage_position(ts, bar_dict, ny_time)
            return

        # Check if can enter new
        ok, reason = can_enter_new(self.day, ny_time)
        if not ok:
            return

        # Detect bull-flag
        signal, params = detect_bull_flag(list(ts.bars))
        if not signal:
            return

        # Pullback-count check (3rd+ pullback skip)
        ts.pullback_count_today += 1
        if ts.pullback_count_today >= 3:
            log.info("%s skip: 3rd+ pullback", sym)
            return

        # Position-Size
        equity = self.executor.get_equity()
        shares = compute_position_size(params["entry_price"], params["stop_price"], equity, self.day)
        if shares < 1:
            log.info("%s skip: shares < 1 (entry %.2f stop %.2f)", sym, params["entry_price"], params["stop_price"])
            return

        # Submit
        order_id = self.executor.submit_buy_limit(sym, shares, params["entry_price"])
        if order_id:
            ts.in_position = True
            ts.entry_price = params["entry_price"]
            ts.stop_price = params["stop_price"]
            ts.target1_price = params["target1"]
            ts.target2_price = params["target2"]
            ts.shares = shares
            ts.pole_candles = params["pole_candles"]
            ts.flag_candles = params["flag_candles"]
            ts.half_filled = False
            self.logger.log({
                "event": "entry", "symbol": sym, "rank": ts.rank, "score": ts.score,
                **params, "shares": shares, "order_id": order_id,
            })

    async def manage_position(self, ts: TickerState, bar: dict, ny_time: dtime):
        # T1
        if not ts.half_filled and bar["high"] >= ts.target1_price:
            half = max(1, ts.shares // 2)
            self.executor.submit_sell_limit(ts.symbol, half, ts.target1_price, "T1_50pct")
            self.logger.log({"event": "T1", "symbol": ts.symbol, "shares": half, "price": ts.target1_price})
            ts.half_filled = True
            ts.shares -= half
            self.day.cents_per_share_cumulative += (ts.target1_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
                log.info("Quarter-Size-Rule UNLOCKED today")
            return
        # T2
        if ts.half_filled and bar["high"] >= ts.target2_price:
            self.executor.submit_sell_limit(ts.symbol, ts.shares, ts.target2_price, "T2")
            self.logger.log({"event": "T2_exit", "symbol": ts.symbol, "shares": ts.shares, "price": ts.target2_price})
            self.day.realized_pnl += (ts.target2_price - ts.entry_price) * ts.shares + (ts.target1_price - ts.entry_price) * (ts.shares)  # approx
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.consecutive_losses = 0
            ts.in_position = False
            return
        # Stop / BE
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            self.executor.submit_sell_limit(ts.symbol, ts.shares, stop, "stop_or_BE")
            pnl = (stop - ts.entry_price) * ts.shares
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-DETECTION: 2 consecutive losses → trading stopped")
            else:
                self.day.consecutive_losses = 0
            self.logger.log({"event": "stop_exit", "symbol": ts.symbol, "shares": ts.shares,
                            "price": stop, "pnl": pnl, "reason": "stop" if not ts.half_filled else "BE"})
            ts.in_position = False
            return


# ─── Replay-Mode (stream historical 5m bars through bot logic) ─────────────
class ReplayBot:
    """Validate bot end-to-end ohne Alpaca-API: streamt pilot intraday_5m durch."""

    def __init__(self):
        self.tickers: dict[str, TickerState] = {}
        self.day = DayState()
        self.logger = TradeLogger()
        self.equity = 25_000.0  # paper-default

    def submit_buy(self, sym, shares, price): log.info("[REPLAY] BUY %s %d @ %.2f", sym, shares, price)
    def submit_sell(self, sym, shares, price, reason): log.info("[REPLAY] SELL %s %d @ %.2f (%s)", sym, shares, price, reason)

    def run(self, target_date: str):
        bars_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "intraday_5m.parquet"
        cands_path = Path(__file__).parent.parent / "04_backtest" / "data_pilot" / "candidates.parquet"
        if not bars_path.exists():
            log.error("Need pilot data — run 04_backtest/bootstrap.py first"); return

        bars = pd.read_parquet(bars_path)
        cands = pd.read_parquet(cands_path)
        # Normalize
        tc = next((c for c in bars.columns if "time" in c.lower() or "date" in c.lower()), None)
        bars[tc] = pd.to_datetime(bars[tc], utc=True)
        bars["session_date"] = bars[tc].dt.tz_convert("America/New_York").dt.date
        target = pd.to_datetime(target_date).date()
        day_bars = bars[bars["session_date"] == target].sort_values(tc)
        if day_bars.empty:
            available = sorted(bars["session_date"].unique())[-10:]
            log.error("No bars for %s. Available: %s", target_date, available); return

        # Pick top-10 from candidates that day
        cands["date"] = pd.to_datetime(cands["date"]).dt.date
        day_cands = cands[cands["date"] == target].copy()
        if day_cands.empty:
            log.error("No candidates for %s in pilot", target_date); return
        day_cands["score"] = day_cands["rvol_proxy"] * day_cands["intraday_pct"]
        top = day_cands.sort_values("score", ascending=False).head(TOP_N)
        log.info("Top-%d for %s: %s", TOP_N, target_date, top["ticker"].tolist())
        for rank, row in enumerate(top.itertuples()):
            self.tickers[row.ticker] = TickerState(symbol=row.ticker, rank=rank+1, score=float(row.score))

        # Stream bars chronologically
        relevant = day_bars[day_bars["ticker"].isin(self.tickers.keys())].sort_values(tc)
        log.info("Streaming %d bars across %d tickers", len(relevant), relevant["ticker"].nunique())

        for _, b in relevant.iterrows():
            sym = b["ticker"]
            ts = self.tickers[sym]
            bar = {"open": b["open"], "high": b["high"], "low": b["low"],
                   "close": b["close"], "volume": b["volume"], "timestamp": b[tc]}
            ts.bars.append(bar)
            ny_t = b[tc].tz_convert("America/New_York").time()

            if ts.in_position:
                self._manage(ts, bar, ny_t); continue

            ok, reason = can_enter_new(self.day, ny_t)
            if not ok: continue
            signal, params = detect_bull_flag(list(ts.bars))
            if not signal: continue
            ts.pullback_count_today += 1
            if ts.pullback_count_today >= 3: continue
            shares = compute_position_size(params["entry_price"], params["stop_price"], self.equity, self.day)
            if shares < 1: continue
            self.submit_buy(sym, shares, params["entry_price"])
            ts.in_position = True
            ts.entry_price = params["entry_price"]; ts.stop_price = params["stop_price"]
            ts.target1_price = params["target1"]; ts.target2_price = params["target2"]
            ts.shares = shares; ts.half_filled = False
            self.logger.log({"event": "REPLAY_entry", "symbol": sym, "rank": ts.rank, **params, "shares": shares})

        # End-of-day report
        log.info("=" * 60)
        log.info("REPLAY DONE — %s", target_date)
        log.info("  Daily realized PnL: $%.2f", self.day.realized_pnl)
        log.info("  Peak PnL:           $%.2f", self.day.peak_pnl)
        log.info("  Consecutive losses: %d (spiral_locked=%s)",
                 self.day.consecutive_losses, self.day.spiral_locked)
        log.info("=" * 60)

    def _manage(self, ts, bar, ny_t):
        # Same logic as Bot.manage_position but synchronous + replay-stubs
        if not ts.half_filled and bar["high"] >= ts.target1_price:
            half = max(1, ts.shares // 2)
            self.submit_sell(ts.symbol, half, ts.target1_price, "T1")
            ts.half_filled = True; ts.shares -= half
            self.day.cents_per_share_cumulative += (ts.target1_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
            return
        if ts.half_filled and bar["high"] >= ts.target2_price:
            self.submit_sell(ts.symbol, ts.shares, ts.target2_price, "T2")
            pnl = (ts.target2_price - ts.entry_price) * ts.shares
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.consecutive_losses = 0
            ts.in_position = False; return
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            self.submit_sell(ts.symbol, ts.shares, stop, "stop")
            pnl = (stop - ts.entry_price) * ts.shares
            self.day.realized_pnl += pnl
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-LOCK after 2 losses")
            else:
                self.day.consecutive_losses = 0
            ts.in_position = False


# ─── Pre-Flight Connection Check ────────────────────────────────────────────
def check_connection():
    """Verifiziert Alpaca-API + Account-Status."""
    api_key = os.environ.get("APCA_API_KEY_ID", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not api_key or not api_secret:
        print("FAIL: APCA_API_KEY_ID + APCA_API_SECRET_KEY nicht gesetzt")
        print("Setup: https://app.alpaca.markets/paper/dashboard/overview")
        return False
    try:
        from alpaca.trading.client import TradingClient
        client = TradingClient(api_key, api_secret, paper=True)
        acc = client.get_account()
        print(f"  Status:        {acc.status}")
        print(f"  Equity:        ${float(acc.equity):,.2f}")
        print(f"  Buying-Power:  ${float(acc.buying_power):,.2f}")
        print(f"  Cash:          ${float(acc.cash):,.2f}")
        print(f"  Pattern-Day:   {acc.pattern_day_trader}")
        print(f"  Trading-Block: {acc.trading_blocked}")
        print(f"  Account-Block: {acc.account_blocked}")
        if acc.trading_blocked or acc.account_blocked:
            print("FAIL: Account blocked")
            return False
        print("OK: Alpaca-Verbindung funktioniert")
        return True
    except Exception as e:
        print(f"FAIL: {e}")
        return False


def status_check():
    """Aktuelle Positions + Daily-PnL anzeigen."""
    api_key = os.environ.get("APCA_API_KEY_ID", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not api_key or not api_secret:
        print("FAIL: APCA_API_KEY_ID + APCA_API_SECRET_KEY nicht gesetzt")
        return
    from alpaca.trading.client import TradingClient
    client = TradingClient(api_key, api_secret, paper=True)
    acc = client.get_account()
    print(f"=== ACCOUNT ===")
    print(f"  Equity: ${float(acc.equity):,.2f}")
    print(f"  Cash:   ${float(acc.cash):,.2f}")
    print(f"  PnL today: ${float(acc.equity) - float(acc.last_equity):+,.2f}")
    print(f"\n=== POSITIONS ===")
    pos = client.get_all_positions()
    if not pos:
        print("  (keine offenen Positionen)")
    for p in pos:
        ul = float(p.unrealized_pl)
        ulpc = float(p.unrealized_plpc) * 100
        print(f"  {p.symbol}: {p.qty} @ ${float(p.avg_entry_price):.2f} → ${float(p.current_price):.2f}  PnL ${ul:+.2f} ({ulpc:+.2f}%)")
    print(f"\n=== TODAY-ORDERS ===")
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus
    today_orders = client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=20))
    for o in today_orders[:20]:
        print(f"  {o.created_at.strftime('%H:%M')} {o.side} {o.qty} {o.symbol} @ ${o.limit_price or 'mkt'} → {o.status}")


# ─── CLI ────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Pattern-Detection only, no orders")
    p.add_argument("--scan-only", action="store_true", help="Premarket-Scan + exit")
    p.add_argument("--replay", type=str, help="Historical replay YYYY-MM-DD aus pilot-data")
    p.add_argument("--check-connection", action="store_true", help="Alpaca-Auth verifizieren")
    p.add_argument("--status", action="store_true", help="Account + Positions anzeigen")
    args = p.parse_args()

    if args.check_connection:
        ok = check_connection()
        sys.exit(0 if ok else 1)

    if args.status:
        status_check()
        return

    if args.replay:
        ReplayBot().run(args.replay)
        return

    if args.scan_only:
        cands = premarket_scan(TOP_N)
        print("\n=== TOP-10 WATCHLIST ===")
        for ts in cands:
            print(f"  rank{ts.rank}: {ts.symbol}  score {ts.score:.1f}")
        return

    api_key = os.environ.get("APCA_API_KEY_ID", "")
    api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not api_key or not api_secret:
        log.error("Set APCA_API_KEY_ID + APCA_API_SECRET_KEY env-vars first.")
        log.error("Or use --replay YYYY-MM-DD for offline-test")
        log.error("Or use --scan-only for pure scanner test")
        log.error("Alpaca paper signup: https://app.alpaca.markets/signup")
        return

    bot = Bot(api_key, api_secret, dry_run=args.dry_run)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
