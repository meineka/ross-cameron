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
from alpaca.trading.requests import (
    LimitOrderRequest, MarketOrderRequest,
    TakeProfitRequest, StopLossRequest, GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# Lokale Module für Verbesserungen
sys.path.insert(0, str(Path(__file__).parent))
from pre_flight import run_preflight
from watchlist_persist import save_watchlist, load_watchlist_if_fresh
from reconnect_backoff import ReconnectBackoff
from slippage_log import record_fill
from status_dashboard import write_status
from day_summary_persist import write_day_summary
from position_recovery import recover_or_flatten
from vwap_filter import is_above_vwap
from float_filter import passes_float_filter
from indicators import macd_is_bullish, macd_bear_cross, false_breakout_veto
from catalyst_filter import passes_catalyst_filter
from delisted_cache import filter_known_delisted, mark_batch_delisted
from pump_dump_filter import size_multiplier as pd_size_multiplier, is_pump_dump_risk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot")
# yfinance-Spam ein-dämmen — delisted-Symbol-ERRORs sind kein Problem,
# sie haben heute 1000+ Logs/Audit-Alarme verursacht.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# ─── Cameron-Constants (mirror constraints.yaml) ────────────────────────────
PRICE_MIN, PRICE_MAX = 2.0, 20.0
DAILY_GAIN_MIN_PCT = 10.0
RVOL_MIN_PROXY = 5.0  # Cameron-strict (war fälschlich 2.0)
FLOAT_MAX_SHARES = 10_000_000  # 5. Cameron-Pillar
CATALYST_REQUIRED = True  # 5. Cameron-Pillar
POWER_HOUR_END = dtime(10, 30)
POWER_HOUR_SIZE_MULT = 1.0
POST_POWER_SIZE_MULT = 0.75
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

LIQUIDITY_CAP_PCT_OF_AVG_VOL = 1.0
QUARTER_SIZE_UNLOCK_CENTS = 0.20

# ── 8 Easy-Wins (Cameron-Compliance) ───────────────────────────────────────
# #4 Daily-Goal-Stop: bei erreichen STOP
DAILY_GOAL_STOP_ENABLED = True

# #5 Max-Trades pro Tag (Cameron-Rule: Quality > Quantity)
MAX_TRADES_PER_DAY = 5             # Cameron sagt 1 für Beginners, 3-5 für ihn selbst

# #2 30¢-Quick-Exit: wenn 30c gegen Entry → exit (mistime-detection)
QUICK_EXIT_THRESHOLD_CENTS = 0.30
QUICK_EXIT_BARS_LIMIT = 5          # innerhalb 5 Bars nach Entry

# #1 Position-Adding (Pyramiding): bei jedem +10¢ höher 25% mehr Shares (max 3 Adds)
ADD_TO_WINNER_ENABLED = True
ADD_TRIGGER_CENTS = 0.10           # alle 10¢ above entry
ADD_FRACTION = 0.25                # 25% extra shares pro Add
MAX_ADDS_PER_TRADE = 3

# #8 Slippage realistisch
SLIPPAGE_CENTS = 0.05              # was 0.01, jetzt realistic 5c

# #6 SPY-Trend-Filter: skip Trading wenn SPY < -1% am Tag (Bear-Day)
SPY_TREND_VETO_PCT = -1.0
SPY_TREND_REDUCE_SIZE_PCT = -0.5   # SPY < -0.5% aber > -1%: size 50%

# #3 Whole/Half-Dollar Targets
USE_PSYCH_LEVEL_T2 = True          # T2 = max(pole_height_target, nearest psych level)

# #7 Pole-Volume-Rising
POLE_VOLUME_RISING_REQUIRED = True

# Time-Cuts (NY-Time)
TIME_NEW_ENTRIES_END = dtime(11, 30)
TIME_HARD_FLAT = dtime(12, 0)
TIME_RTH_START = dtime(9, 30)
TIME_NEW_ENTRIES_START = dtime(9, 35)

# Re-Scan-Strategie: zwei Schichten, ALIGNED zu round-5-Min boundaries
# SLOW: yfinance Universe-Pull, ~3 Min Laufzeit → 180 Sek Head-Start
# FAST: Alpaca Snapshot, <1 Sek → 5 Sek Head-Start
SCAN_HEAD_START_SLOW_SEC = 180  # yfinance scan dauer
SCAN_HEAD_START_FAST_SEC = 5    # Alpaca snapshot dauer
RESCAN_SLOW_INTERVAL_MIN = 5    # alle 5 Min finish bei :00, :05, :10, :15, :20...
RESCAN_FAST_INTERVAL_MIN = 1    # alle 1 Min Alpaca-Re-Rank
RESCAN_FAST_PHASE_END = dtime(10, 30)  # Power-Hour Ende


def aligned_scan_start(now: datetime, period_min: int, head_start_sec: int) -> datetime:
    """Returns next datetime where scan must START to FINISH at next round boundary.

    Beispiel: period_min=5, head_start=180 → finish bei :00, :05, :10, :15, ...
    Start bei :02:00, :07:00, :12:00, :17:00, :22:00, :27:00, :32:00 ...
    """
    minutes_past = now.minute % period_min
    next_boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=period_min - minutes_past)
    start = next_boundary - timedelta(seconds=head_start_sec)
    if start <= now:
        start = next_boundary + timedelta(minutes=period_min) - timedelta(seconds=head_start_sec)
    return start

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
    entry_bar_idx: int = 0          # bar-count at entry (for #2 quick-exit)
    bars_since_entry: int = 0       # counter
    stop_price: float = 0.0
    target1_price: float = 0.0
    target2_price: float = 0.0
    half_filled: bool = False
    shares: int = 0
    initial_shares: int = 0         # for pyramiding-tracking
    adds_count: int = 0             # #1 add-counter
    last_add_price: float = 0.0
    pole_candles: int = 0
    flag_candles: int = 0
    pole_height: float = 0.0


@dataclass
class DayState:
    date: str = ""
    realized_pnl: float = 0.0
    peak_pnl: float = 0.0
    consecutive_losses: int = 0
    quarter_size_unlocked: bool = False
    cents_per_share_cumulative: float = 0.0
    spiral_locked: bool = False
    # Telemetry counters
    bars_received: int = 0
    patterns_detected: int = 0
    patterns_rejected_macd: int = 0
    patterns_rejected_fbo: int = 0
    patterns_rejected_pullback_count: int = 0
    patterns_rejected_size_zero: int = 0
    patterns_rejected_max_trades: int = 0    # #5
    orders_submitted: int = 0
    orders_failed: int = 0
    adds_executed: int = 0                    # #1
    quick_exits: int = 0                       # #2
    ws_reconnects: int = 0
    last_heartbeat: datetime | None = None
    # #5 Trade-Counter
    trades_completed_today: int = 0
    # #6 SPY-Trend
    spy_pct_today: float = 0.0
    spy_size_multiplier: float = 1.0          # 1.0=normal, 0.5=halved, 0.0=skip
    # #4 daily-goal-stop
    goal_reached: bool = False


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


def premarket_scan(top_n: int = TOP_N, max_retries: int = 2) -> list[TickerState]:
    """5-Pillars-Filter + Top-N-Composite-Score-Ranking, mit Retry-Logik."""
    for attempt in range(max_retries + 1):
        try:
            return _premarket_scan_inner(top_n)
        except Exception as e:
            log.error("Scan attempt %d failed: %s", attempt + 1, e, exc_info=True)
            if attempt < max_retries:
                wait = 60 * (attempt + 1)
                log.info("  Retry in %d sec…", wait)
                import time as _t; _t.sleep(wait)
    log.error("Scanner failed after %d attempts — returning empty watchlist", max_retries + 1)
    return []


def _premarket_scan_inner(top_n: int) -> list[TickerState]:
    log.info("=" * 60)
    log.info("PREMARKET SCAN START — pulling daily bars")
    log.info("=" * 60)
    tickers = fetch_us_universe()
    log.info("  Universe: %d tickers from NASDAQ-Trader CSV", len(tickers))
    if not tickers:
        log.error("  FAIL: empty universe — NASDAQ-Trader CSV unreachable?")
        return []
    # Delisted-Cache filtern (Fix vom 2026-05-12: yfinance-Spam-Prävention)
    tickers, skipped_delisted = filter_known_delisted(tickers)
    if skipped_delisted:
        log.info("  Skipped %d known-delisted tickers from cache", skipped_delisted)

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=45)
    cands = []
    batch_size = 200
    n_batches = (len(tickers) + batch_size - 1) // batch_size
    failed_batches = 0
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        batch_idx = i // batch_size + 1
        try:
            df = yf.download(
                tickers=batch, start=start.isoformat(), end=end.isoformat(),
                interval="1d", group_by="ticker", auto_adjust=False,
                progress=False, threads=True,
            )
        except Exception as e:
            log.warning("  Batch %d/%d FAIL: %s", batch_idx, n_batches, e)
            failed_batches += 1
            continue
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=0, future_stack=True).rename_axis(["date","ticker"]).reset_index()
        else:
            df = df.reset_index(); df["ticker"] = batch[0]
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        df = df.dropna(subset=["close","open","volume"])
        # Tickers ohne Daten als delisted markieren (Fix vom 2026-05-12)
        try:
            seen = set(df["ticker"].unique()) if "ticker" in df.columns else set()
            dead_in_batch = [t for t in batch if t not in seen]
            if dead_in_batch:
                mark_batch_delisted(dead_in_batch)
        except Exception:
            pass
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
        if batch_idx % 5 == 0:
            cumulative = sum(len(c) for c in cands)
            log.info("  Batch %d/%d processed; %d candidates so far", batch_idx, n_batches, cumulative)

    log.info("  Failed batches: %d/%d", failed_batches, n_batches)
    if not cands:
        log.warning("  NO CANDIDATES found — empty result. Possible reasons:")
        log.warning("    - market closed today (holiday/weekend)")
        log.warning("    - 5-pillars filter too strict for current market")
        log.warning("    - yfinance data issues")
        return []

    all_cands = pd.concat(cands, ignore_index=True)
    log.info("  Pre-rank candidates: %d (price/RVOL/%%)", len(all_cands))

    # Cameron-Pillar 2 (Float) + Pillar 5 (Catalyst) — die fehlenden zwei
    all_cands["score"] = all_cands["rvol_proxy"] * all_cands["intraday_pct"]
    all_cands = all_cands.sort_values("score", ascending=False).head(top_n * 3)

    filtered = []
    for row in all_cands.itertuples():
        sym = row.ticker
        if not passes_float_filter(sym, FLOAT_MAX_SHARES):
            log.info("    REJECT %s (float > %s)", sym, f"{FLOAT_MAX_SHARES:,}")
            continue
        if CATALYST_REQUIRED and not passes_catalyst_filter(sym):
            log.info("    REJECT %s (no recent catalyst)", sym)
            continue
        filtered.append(row)
        if len(filtered) >= top_n:
            break
    log.info("  Post-Float+Catalyst-Filter: %d / %d", len(filtered), len(all_cands))
    all_cands = pd.DataFrame(filtered) if filtered else all_cands.head(0)
    log.info("=" * 60)
    log.info("TOP-%d WATCHLIST:", top_n)
    for rank, row in enumerate(all_cands.itertuples(), start=1):
        log.info("  #%d %s  $%.2f  +%.1f%%  RVOL %.1fx  score=%.0f",
                 rank, row.ticker, row.close, row.intraday_pct, row.rvol_proxy, row.score)
    log.info("=" * 60)
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
            # #7 Pole-Volume-Rising: Volume in 2nd half of pole >= 1st half (avg)
            if POLE_VOLUME_RISING_REQUIRED and pl >= 4:
                first_half_vol = v[ps:ps+pl//2].mean()
                second_half_vol = v[ps+pl//2:pe].mean()
                if second_half_vol < first_half_vol * 0.9:  # 10% Toleranz
                    continue
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
            # #3 T2 = max(pole_height-target, next psych. level above entry)
            t2_mech = ep + p_h
            if USE_PSYCH_LEVEL_T2:
                # nächste 0.50/1.00 above entry
                next_half = (int(ep * 2) + 1) / 2.0   # nächstes 0.50
                t2 = max(t2_mech, next_half) if next_half > ep + 0.05 else t2_mech
            else:
                t2 = t2_mech
            # ─── Cameron-Vetos (heute gefixt) ─────────────────────────────
            # VWAP: Cameron tradet nur über Session-VWAP
            if not is_above_vwap(bars, c[i]):
                return False, {"_veto": "vwap"}
            # MACD 12/26/9: bullish + over zero-line
            if not macd_is_bullish(c.tolist()):
                return False, {"_veto": "macd"}
            # FBO-5-Indicator
            vetoed, why = false_breakout_veto(bars)
            if vetoed:
                return False, {"_veto": f"fbo_{why}"}

            return True, {
                "entry_price": float(ep),
                "stop_price": float(sp),
                "target1": float(ep + (ep - sp)),
                "target2": float(t2),
                "pole_height": float(p_h),
                "pole_candles": int(pl),
                "flag_candles": int(fl),
            }
    return False, {}


# ─── Risk-Engine ────────────────────────────────────────────────────────────
def compute_position_size(
    entry: float, stop: float, account_equity: float, day: DayState,
    *, avg_volume: float | None = None, ny_time: dtime | None = None,
) -> int:
    """Position-Sizing mit Cameron-Regeln. Defensiv gegen pathologische Inputs.

    Audit-Bug-Fix 2026-05-12:
      - Bug A: account_equity wurde ignoriert → 1 % Equity-Cap erzwingen
      - Bug B: winziger risk_per_share (<5¢) → explodierende Position → Minimum-Stop $0.05
      - Bug C: negative/null Eingaben → 0 shares (defensive)
    """
    # Defensive: ungültige Inputs
    if entry <= 0 or stop <= 0:
        return 0
    if entry <= stop:
        return 0
    raw_risk_per_share = entry - stop
    # Minimum-Stop $0.05 (5 cents): bei engerem Stop ist Pattern-Detection
    # vermutlich Artefakt — verhindert 50000-Shares-Position
    risk_per_share = max(raw_risk_per_share, 0.05)
    max_shares = int(MAX_LOSS_PER_TRADE_USD / risk_per_share)
    # Equity-Cap: max 1 % von account_equity riskieren (Cameron-Rule)
    if account_equity and account_equity > 0:
        equity_risk_cap = account_equity * 0.01
        max_shares = min(max_shares, int(equity_risk_cap / risk_per_share))
    # Quarter-Size-Rule
    if not day.quarter_size_unlocked:
        max_shares = max_shares // 4
    # Power-Hour-Boost: 9:30-10:30 full, danach 75 %
    if ny_time is not None:
        mult = POWER_HOUR_SIZE_MULT if ny_time < POWER_HOUR_END else POST_POWER_SIZE_MULT
        max_shares = int(max_shares * mult)
    # Liquidity-Cap: max 1 % of avg-daily-volume (Cameron 'Whales-in-Pond')
    if avg_volume and avg_volume > 0:
        cap = int(avg_volume * LIQUIDITY_CAP_PCT_OF_AVG_VOL / 100)
        max_shares = min(max_shares, cap)
    return max(0, max_shares)


def can_enter_new(day: DayState, ny_time: dtime) -> tuple[bool, str]:
    if day.spiral_locked: return False, "spiral_locked"
    if day.realized_pnl <= -DAILY_MAX_LOSS_USD: return False, "daily_max_loss"
    # #4 Daily-Goal-Stop
    if DAILY_GOAL_STOP_ENABLED and day.goal_reached: return False, "daily_goal_reached"
    if day.peak_pnl > 0 and day.realized_pnl < day.peak_pnl * (1 - INTRADAY_DRAWDOWN_PCT_OF_PROFITS/100):
        return False, "intraday_drawdown_50pct"
    if ny_time >= TIME_NEW_ENTRIES_END: return False, "after_1130"
    if ny_time < TIME_RTH_START: return False, "before_rth"
    if ny_time < TIME_NEW_ENTRIES_START: return False, "open_range_5min"  # Fix 12.05: kein Entry in 1. 5min
    # #5 Max trades per day
    if day.trades_completed_today >= MAX_TRADES_PER_DAY:
        return False, f"max_{MAX_TRADES_PER_DAY}_trades_today"
    # #6 SPY-Trend-Filter (when reduce 0.5x is set, allow but smaller; when 0.0 skip)
    if day.spy_size_multiplier <= 0.0:
        return False, f"SPY_bear_day_{day.spy_pct_today:+.2f}%"
    return True, ""


def fetch_spy_today_pct() -> float:
    """SPY heutiger Move (vs prev close) für Bear-Day-Filter."""
    try:
        df = yf.download("SPY", period="2d", interval="1d", progress=False, auto_adjust=False)
        if df.empty or len(df) < 2: return 0.0
        prev = float(df["Close"].iloc[-2])
        cur = float(df["Close"].iloc[-1])
        return (cur - prev) / prev * 100
    except Exception:
        return 0.0


def compute_spy_size_multiplier(spy_pct: float) -> float:
    """SPY-Trend-basierter Size-Multiplier."""
    if spy_pct <= SPY_TREND_VETO_PCT:
        return 0.0    # Bear day: skip
    if spy_pct <= SPY_TREND_REDUCE_SIZE_PCT:
        return 0.5    # weak day: half size
    return 1.0        # normal


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
        """Plain Limit-Buy (für Pyramiding-Adds — Stop/TP der Hauptposition liegt
        bereits broker-seitig als Bracket)."""
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

    def submit_bracket_buy(self, symbol: str, shares: int, entry: float,
                           stop: float, take_profit: float) -> str | None:
        """Cameron-Default: Entry-Limit + Stop-Loss + Take-Profit als BRACKET.
        Alle drei broker-seitig — Position ist NIE 'nackt' wenn entry fillt.

        Bug-Fix 2026-05-12: Sanity-Check vor submit. Wenn stop >= entry oder
        tp <= entry → invalid order, log + skip. Post-Fill-Check macht
        manage_position-Loop (next_bar bemerkt Position + setzt fresh OCO
        wenn nötig). Bei thin-liquidity Stocks mit Gap-Fill ist das die
        einzige Methode den HSPT-Bug zu vermeiden.
        """
        if stop >= entry:
            log.error("BRACKET-BUY %s INVALID: stop %.2f >= entry %.2f — skip", symbol, stop, entry)
            return None
        if take_profit <= entry:
            log.error("BRACKET-BUY %s INVALID: tp %.2f <= entry %.2f — skip", symbol, take_profit, entry)
            return None
        if self.dry_run:
            log.info("[DRY] BRACKET-BUY %s %d entry=%.2f stop=%.2f tp=%.2f",
                     symbol, shares, entry, stop, take_profit)
            return f"dryrun-{symbol}-{datetime.now().timestamp()}"
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(entry, 2),
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            )
            o = self.client.submit_order(req)
            log.info("BRACKET-BUY %s %d entry=%.2f STOP=%.2f TP=%.2f → %s",
                     symbol, shares, entry, stop, take_profit, o.id)
            return o.id
        except Exception as e:
            log.error("submit_bracket_buy err %s: %s", symbol, e)
            return None

    def verify_and_repair_protection(self, symbol: str, fill_price: float,
                                     planned_stop: float, planned_tp: float,
                                     shares: int) -> bool:
        """Nach BRACKET-Fill: check ob Stop < Fill (Long-Validität).
        Wenn nicht → cancel Bracket-Children + neue OCO mit Stop unter Fill.
        Verhindert HSPT/ATRA-Bug. Returns True wenn repariert."""
        if planned_stop < fill_price:
            return False  # alles ok
        if self.dry_run:
            return False
        log.warning("REPAIR %s: fill %.4f below planned stop %.2f — re-bracketing",
                    symbol, fill_price, planned_stop)
        # Bracket-Children weg
        try:
            from alpaca.trading.requests import GetOrdersRequest
            opens = self.client.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol], limit=20,
            ))
            for child in opens:
                try: self.client.cancel_order_by_id(child.id)
                except Exception: pass
            import time as _t; _t.sleep(2)
        except Exception:
            pass
        # OCO relativ zum FILL
        new_stop = round(fill_price * 0.95, 2)
        new_tp = round(fill_price + 2 * (fill_price - new_stop), 2)
        try:
            self.client.submit_order(LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=new_tp,
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=new_tp),
                stop_loss=StopLossRequest(stop_price=new_stop),
            ))
            log.info("  REPAIR-OCO %s: stop=%.2f tp=%.2f", symbol, new_stop, new_tp)
            return True
        except Exception as e:
            log.error("REPAIR failed for %s: %s", symbol, e)
            return False

    def protect_position(self, symbol: str, shares: int, stop: float, take_profit: float) -> bool:
        """Setze für eine bestehende long-Position broker-seitig OCO-Schutz
        (Stop + Take-Profit). Nötig nach T1-Partial oder Pyramiding-Add.

        Audit-Iter 7 (2026-05-12) — CRITICAL Bug-Fix BO-1/BO-3:
          Vorher zwei separate Orders (StopOrder + LimitOrder). Wenn Stop fillt,
          blieb TP offen → wenn Preis das TP-Level später streifte → OVERSOLD
          (Account wurde SHORT). Jetzt OCO: One-Cancels-Other atomic, kein
          Drift möglich. Sanity-Check stop < take_profit + Validity-Guards.

        Returns: True wenn protection-Order beim Broker ist, False sonst."""
        if self.dry_run:
            log.info("[DRY] PROTECT %s %d  STOP=%.2f  TP=%.2f",
                     symbol, shares, stop, take_profit)
            return True
        if shares < 1:
            log.warning("protect_position: shares=%d for %s — skip", shares, symbol)
            return False
        if stop >= take_profit:
            log.error("protect_position %s INVALID: stop %.2f >= tp %.2f — skip",
                      symbol, stop, take_profit)
            return False
        # Alte Schutz-Orders weg, damit Quantity passt
        self.cancel_open_orders_for(symbol)
        # OCO atomic Schutz — eines greift, das andere wird auto-cancelled
        try:
            self.client.submit_order(LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=round(take_profit, 2),
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            ))
            log.info("  PROTECT-OCO %s %d  STOP=%.2f  TP=%.2f",
                     symbol, shares, stop, take_profit)
            return True
        except Exception as e:
            log.error("protect-OCO %s failed: %s — falling back to separate stop+tp",
                      symbol, e)
            # Fallback: separate orders. Risiko von oversold im Edge-Case
            # akzeptiert vs. Risiko von unprotected position.
            ok_stop = False
            try:
                from alpaca.trading.requests import StopOrderRequest
                self.client.submit_order(StopOrderRequest(
                    symbol=symbol, qty=shares, side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY, stop_price=round(stop, 2),
                ))
                ok_stop = True
            except Exception as e2:
                log.error("fallback-stop %s err: %s", symbol, e2)
            try:
                self.client.submit_order(LimitOrderRequest(
                    symbol=symbol, qty=shares, side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                    limit_price=round(take_profit, 2),
                ))
            except Exception as e2:
                log.error("fallback-tp %s err: %s", symbol, e2)
            return ok_stop  # mindestens Stop muss stehen

    def cancel_open_orders_for(self, symbol: str,
                                wait_seconds: float = 3.0,
                                poll_interval: float = 0.3) -> int:
        """Cancel alle offenen Orders eines Symbols und warte bis sie wirklich
        weg sind — nötig vor T1-Partial oder Quick-Exit damit Bracket-Children
        nicht doppelt feuern.

        Audit-Iter 8 (2026-05-12) — Bug-Fix BO-6/BO-7:
          Vorher: submit cancel + sofort return. Alpaca processiert Cancels
          async (state: OPEN → PENDING_CANCEL → CANCELED). Folge-submit konnte
          während PENDING_CANCEL feuern → beide Orders alive → oversold.
        Jetzt: nach Cancel-Submit Polling bis alle Target-IDs nicht mehr OPEN
        sind, oder bis wait_seconds Timeout. Logged Failures statt swallow.

        Returns: Anzahl Cancels die submitted (nicht zwingend confirmed)
                 wurden. Bei wait_seconds=0 wird nicht gewartet (Legacy).
        """
        if self.dry_run:
            return 0
        try:
            opens = self.client.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol], limit=50,
            ))
        except Exception as e:
            log.warning("cancel_open_orders list err for %s: %s", symbol, e)
            return 0
        target_ids: set[str] = set()
        failed: list[tuple[str, str]] = []
        for o in opens or []:
            try:
                self.client.cancel_order_by_id(o.id)
                target_ids.add(o.id)
            except Exception as e:
                failed.append((str(o.id), str(e)))
        if failed:
            log.warning("cancel_open_orders %s: %d cancels failed: %s",
                        symbol, len(failed), failed)
        submitted = len(target_ids)
        if submitted == 0:
            return 0
        # Poll bis target-IDs nicht mehr OPEN sind
        if wait_seconds > 0:
            import time as _t
            deadline = _t.time() + wait_seconds
            while _t.time() < deadline:
                try:
                    still = self.client.get_orders(filter=GetOrdersRequest(
                        status=QueryOrderStatus.OPEN, symbols=[symbol], limit=50,
                    )) or []
                    still_ids = {str(x.id) for x in still}
                    if not (target_ids & still_ids):
                        log.info("  Cancelled %d orders for %s (confirmed)",
                                 submitted, symbol)
                        return submitted
                except Exception:
                    pass
                _t.sleep(poll_interval)
            log.warning("cancel_open_orders %s: timeout — %d may still be live",
                        symbol, len(target_ids))
        else:
            log.info("  Cancelled %d orders for %s (no-wait)", submitted, symbol)
        return submitted

    def submit_sell_limit(self, symbol: str, shares: int, price: float, reason: str) -> str | None:
        """Vor jedem Script-side Sell: erst Bracket-Children canceln damit nicht
        Stop+TP gleichzeitig mit unserem Sell feuern. Im dry-run skip cancel."""
        if self.dry_run:
            log.info("[DRY] SELL %s %d @ %.2f (%s)", symbol, shares, price, reason)
            return f"dryrun-{symbol}-{datetime.now().timestamp()}"
        # Bracket-Children weg, dann unser Sell
        self.cancel_open_orders_for(symbol)
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

    def market_close_all(self, max_attempts: int = 3,
                         verify_timeout_sec: float = 30.0,
                         poll_interval_sec: float = 1.5):
        """HARD_FLAT failsafe — kritisch. Audit-Fix 2026-05-12 (Iteration 5):
          - Bug HF-1: Single-shot ohne retry → jetzt 3 Attempts mit Backoff
          - Bug HF-2: Keine Fill-Verification → jetzt Polling bis positions==0
          - Bug HF-9: Bei finalem Fail per-Position individual close
        Returns: True wenn Account am Ende flat, False sonst (CRITICAL)."""
        if self.dry_run:
            log.info("[DRY] CLOSE ALL"); return True
        import time as _t

        def _list_positions():
            try:
                return list(self.client.get_all_positions() or [])
            except Exception as e:
                log.warning("list positions err: %s", e)
                return None  # unknown — assume not flat

        pre = _list_positions()
        if pre == []:
            log.info("market_close_all: account already flat")
            return True
        if pre is None:
            log.warning("market_close_all: pre-list failed — proceeding blindly")
        else:
            log.info("market_close_all: %d positions to close: %s",
                     len(pre), [getattr(p, "symbol", "?") for p in pre])

        for attempt in range(1, max_attempts + 1):
            try:
                self.client.close_all_positions(cancel_orders=True)
                log.info("close_all_positions submitted (attempt %d/%d)",
                         attempt, max_attempts)
            except Exception as e:
                log.error("close_all err (attempt %d/%d): %s",
                          attempt, max_attempts, e)
            # Poll bis flat oder Timeout
            deadline = _t.time() + verify_timeout_sec
            while _t.time() < deadline:
                cur = _list_positions()
                if cur == []:
                    log.info("market_close_all: account FLAT after attempt %d", attempt)
                    return True
                _t.sleep(poll_interval_sec)
            # noch nicht flat → nächster Attempt
            remaining = _list_positions()
            log.warning("market_close_all: still %s positions after attempt %d",
                        len(remaining) if remaining is not None else "?", attempt)

        # Final Fallback: per-Position individual market-sell
        log.error("market_close_all: %d attempts failed — per-position fallback",
                  max_attempts)
        leftover = _list_positions() or []
        ok = 0
        for p in leftover:
            sym = getattr(p, "symbol", None)
            qty = abs(int(float(getattr(p, "qty", 0) or 0)))
            if not sym or qty <= 0:
                continue
            try:
                self.cancel_open_orders_for(sym)
                from alpaca.trading.requests import MarketOrderRequest
                self.client.submit_order(MarketOrderRequest(
                    symbol=sym, qty=qty, side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                ))
                log.warning("FALLBACK market-sell %s %d submitted", sym, qty)
                ok += 1
            except Exception as e:
                log.error("FALLBACK close %s failed: %s", sym, e)
        # Final verify
        _t.sleep(3.0)
        final = _list_positions()
        if final == []:
            log.info("market_close_all: account FLAT after fallback (%d submitted)", ok)
            return True
        log.error("market_close_all: CRITICAL — account NOT flat after all attempts: %s",
                  [getattr(p, "symbol", "?") for p in (final or [])])
        return False


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

        # 0. Connection Pre-Check
        try:
            equity = self.executor.get_equity()
            log.info("Alpaca-Connection OK — Account-Equity: $%.2f", equity)
        except Exception as e:
            log.error("Alpaca-Connection FAIL: %s", e, exc_info=True)
            return

        # 0a. #6 SPY-Trend-Filter
        spy_pct = await asyncio.to_thread(fetch_spy_today_pct)
        self.day.spy_pct_today = spy_pct
        self.day.spy_size_multiplier = compute_spy_size_multiplier(spy_pct)
        log.info("SPY today: %+.2f%% → size-multiplier %.2fx",
                 spy_pct, self.day.spy_size_multiplier)
        if self.day.spy_size_multiplier <= 0.0:
            log.warning("=" * 60)
            log.warning("SPY-BEAR-DAY: %.2f%% < %.1f%% — KEINE neuen Trades heute",
                        spy_pct, SPY_TREND_VETO_PCT)
            log.warning("=" * 60)

        # 1. Premarket-Scan
        candidates = await asyncio.to_thread(premarket_scan, TOP_N)
        if not candidates:
            log.warning("=" * 60)
            log.warning("KEINE WATCHLIST heute — wahrscheinlich Markt-Holiday oder Filter zu streng")
            log.warning("Bot beendet diesen Trading-Tag, schlaeft bis morgen")
            log.warning("=" * 60)
            return
        for ts in candidates:
            self.tickers[ts.symbol] = ts
            self.logger.log({"event": "watchlist", **asdict(ts), "bars": []})
        try:
            save_watchlist(
                [ts.symbol for ts in candidates],
                {ts.symbol: ts.score for ts in candidates},
            )
            log.info("Watchlist persisted → watchlist_today.json")
        except Exception as e:
            log.warning("watchlist-persist failed: %s", e)

        # 2. Live Bar-Stream Subscribe
        log.info("Subscribing to Alpaca-WS for %d symbols (IEX-Feed)…", len(self.tickers))

        async def on_bar(bar):
            self.day.bars_received += 1
            try:
                await self.handle_bar(bar)
            except Exception as e:
                log.error("handle_bar error for %s: %s", bar.symbol, e, exc_info=True)

        # 3. Time-Cuts + Health-Check + Intraday-Re-Scan Loop
        self._pending_ws_resubscribe = False

        async def time_and_health_loop():
            ny0 = datetime.now(NY_TZ)
            last_health = ny0
            slow_next_at = aligned_scan_start(ny0, RESCAN_SLOW_INTERVAL_MIN, SCAN_HEAD_START_SLOW_SEC)
            fast_next_at = aligned_scan_start(ny0, RESCAN_FAST_INTERVAL_MIN, SCAN_HEAD_START_FAST_SEC)
            log.info("Aligned-Schedule:")
            log.info("  SLOW yfinance: next start at %s ET (finishes ~:%02d:00)",
                     slow_next_at.strftime("%H:%M:%S"),
                     (slow_next_at + timedelta(seconds=SCAN_HEAD_START_SLOW_SEC)).minute)
            log.info("  FAST alpaca:   next start at %s ET", fast_next_at.strftime("%H:%M:%S"))
            while True:
                ny = datetime.now(NY_TZ)
                # Hard-Flat
                if ny.time() >= TIME_HARD_FLAT:
                    log.info("=" * 60)
                    log.info("12:00 ET (18:00 CET) — HARD FLAT")
                    log.info("=" * 60)
                    self.executor.market_close_all()
                    self._log_day_summary()
                    await asyncio.sleep(60)
                    return
                # SLOW Re-Scan (aligned to 5-min boundary, finishes AT round time)
                if ny >= slow_next_at:
                    await self.intraday_rescan()
                    slow_next_at = aligned_scan_start(datetime.now(NY_TZ),
                                                     RESCAN_SLOW_INTERVAL_MIN,
                                                     SCAN_HEAD_START_SLOW_SEC)
                # FAST Re-Scan (aligned to 1-min boundary)
                if ny >= fast_next_at:
                    await self.fast_rescan_via_alpaca()
                    fast_next_at = aligned_scan_start(datetime.now(NY_TZ),
                                                     RESCAN_FAST_INTERVAL_MIN,
                                                     SCAN_HEAD_START_FAST_SEC)
                # Health-Check alle 15 Min
                if (ny - last_health).total_seconds() >= 900:
                    self._log_health()
                    last_health = ny
                # Status-Dashboard alle 30 Sek
                try:
                    write_status(self)
                except Exception:
                    pass
                # Heartbeat-File aktualisieren auch im Trading-Loop (Fix 12.05)
                try:
                    hb_file = Path(__file__).parent / "heartbeat.txt"
                    hb_file.write_text(
                        datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S NY (trading)"),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                await asyncio.sleep(2)  # tighter loop für besseres Alignment

        # 4. WebSocket mit Auto-Reconnect + Exponential-Backoff + Circuit-Breaker
        backoff = ReconnectBackoff(base_sec=1.0, cap_sec=60.0, max_consec_fails=8)
        async def ws_loop():
            """Audit-Bug-Fix 2026-05-12 (Iteration 3):
              - Bug D: ws_reconnects zählt jetzt JEDEN Reconnect (success + fail)
              - Bug E: backoff.sleep nur nach echtem Fehler, nicht nach clean disconnect
            """
            while True:
                had_error = False
                try:
                    ws = StockDataStream(self.api_key, self.api_secret, feed=DataFeed.IEX)
                    current_symbols = list(self.tickers.keys())
                    ws.subscribe_bars(on_bar, *current_symbols)
                    log.info("WS subscribed to %d symbols: %s", len(current_symbols), current_symbols)
                    self._pending_ws_resubscribe = False
                    backoff.reset()  # successful subscribe → reset Backoff

                    # Run WS in thread + monitor for re-subscribe-flag
                    ws_task = asyncio.create_task(asyncio.to_thread(ws.run))
                    while not ws_task.done():
                        await asyncio.sleep(5)
                        if self._pending_ws_resubscribe:
                            log.info("  WS re-subscribe triggered — restarting connection")
                            try:
                                ws.stop_ws()
                            except Exception as e:
                                log.warning("ws.stop_ws() raised: %s", e)
                            break
                    log.warning("WS disconnected — reconnect (clean)")
                    self.day.ws_reconnects += 1
                except Exception as e:
                    had_error = True
                    self.day.ws_reconnects += 1
                    log.error("WS error (#%d): %s", self.day.ws_reconnects, e, exc_info=True)
                if datetime.now(NY_TZ).time() >= TIME_HARD_FLAT:
                    return
                # Backoff/Circuit-Breaker NUR nach Fehler — saubere Disconnects
                # sollen den Counter nicht zum Circuit-Breaker treiben
                if had_error:
                    try:
                        await backoff.sleep_after_fail()
                    except RuntimeError as cb:
                        log.critical("WS Circuit-Breaker: %s — exit ws_loop", cb)
                        return
                else:
                    await asyncio.sleep(1)  # short pause vor reconnect

        try:
            await asyncio.gather(ws_loop(), time_and_health_loop())
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — closing all positions")
            self.executor.market_close_all()
            self._log_day_summary()
        except Exception as e:
            log.error("Bot.run unhandled error: %s", e, exc_info=True)
            self.executor.market_close_all()
            self._log_day_summary()

    async def fast_rescan_via_alpaca(self):
        """Fast-Re-Rank via Alpaca-Snapshot für aktuelle Watchlist + naher Pool."""
        from alpaca.data.requests import StockSnapshotRequest
        try:
            # Snapshot für aktuelle Watchlist
            symbols = list(self.tickers.keys())
            if not symbols:
                return
            data_client = StockHistoricalDataClient(self.api_key, self.api_secret)
            req = StockSnapshotRequest(symbol_or_symbols=symbols)
            snaps = data_client.get_stock_snapshot(req)
            log.info("FAST RESCAN @ %s — Alpaca-snapshot for %d symbols",
                     datetime.now(NY_TZ).strftime("%H:%M ET"), len(snaps))
            for sym, snap in snaps.items():
                if sym not in self.tickers: continue
                bar = snap.daily_bar
                if bar and bar.close:
                    prev = snap.previous_daily_bar
                    if prev and prev.close:
                        intraday_pct = (bar.high - prev.close) / prev.close * 100
                        new_score = bar.volume / max(prev.volume, 1) * intraday_pct
                        old_score = self.tickers[sym].score
                        if abs(new_score - old_score) > old_score * 0.2:
                            log.info("  SCORE CHANGE %s: %.0f → %.0f (%+.1f%%)",
                                     sym, old_score, new_score,
                                     (new_score - old_score) / old_score * 100 if old_score else 0)
                            self.tickers[sym].score = new_score
            # Re-rank within current watchlist
            sorted_syms = sorted(self.tickers.values(), key=lambda x: -x.score)
            for new_rank, ts in enumerate(sorted_syms, start=1):
                if ts.rank != new_rank:
                    log.info("  RE-RANK %s: #%d → #%d", ts.symbol, ts.rank, new_rank)
                    ts.rank = new_rank
        except Exception as e:
            log.warning("fast rescan failed: %s", e)

    async def intraday_rescan(self):
        """Slow Re-scan via yfinance Universe-Pull."""
        log.info("─" * 60)
        log.info("SLOW RE-SCAN @ %s (yfinance universe pull)",
                 datetime.now(NY_TZ).strftime("%H:%M ET"))
        try:
            new_candidates = await asyncio.to_thread(premarket_scan, TOP_N)
        except Exception as e:
            log.error("intraday rescan failed: %s", e, exc_info=True)
            return
        if not new_candidates:
            log.warning("  Re-scan empty — keeping current watchlist")
            return

        new_symbols = {c.symbol for c in new_candidates}
        old_symbols = set(self.tickers.keys())
        added = new_symbols - old_symbols
        removed = old_symbols - new_symbols
        kept = new_symbols & old_symbols

        log.info("  Watchlist delta: +%d added, -%d removed (or held), %d unchanged",
                 len(added), len(removed), len(kept))

        # Update ranks für gehaltene Symbole (Reihenfolge kann sich ändern!)
        for c in new_candidates:
            if c.symbol in self.tickers:
                old_rank = self.tickers[c.symbol].rank
                self.tickers[c.symbol].rank = c.rank
                self.tickers[c.symbol].score = c.score
                if old_rank != c.rank:
                    log.info("  RANK CHANGE %s: #%d → #%d", c.symbol, old_rank, c.rank)

        # Drop-Outs: behalten wenn in Position, sonst entfernen
        for sym in removed:
            ts = self.tickers[sym]
            if ts.in_position:
                log.info("  KEEP %s (out of top-10 but in_position, rank=%d)", sym, ts.rank)
            else:
                log.info("  DROP %s (out of top-10, no position)", sym)
                del self.tickers[sym]
                # Pending: WebSocket-unsubscribe — wird bei nächstem WS-Reconnect implizit gemacht
                self._pending_ws_resubscribe = True

        # Neue Symbole hinzufügen
        for c in new_candidates:
            if c.symbol in added:
                self.tickers[c.symbol] = c
                log.info("  ADD %s rank=#%d score=%.0f — subscribing WS", c.symbol, c.rank, c.score)
                self._pending_ws_resubscribe = True

        log.info("  Current watchlist: %s", [
            f"#{t.rank}{t.symbol}{'*' if t.in_position else ''}"
            for t in sorted(self.tickers.values(), key=lambda x: x.rank)
        ])
        log.info("─" * 60)

    def _log_health(self):
        """Periodic health-check log."""
        # Heartbeat-File auch während Trading-Tag aktualisieren (Fix 12.05:
        # Audit fired RESTART_HEARTBEAT_STALE während bot.run() aktiv war)
        try:
            hb_file = Path(__file__).parent / "heartbeat.txt"
            hb_file.write_text(
                datetime.now(NY_TZ).strftime("%Y-%m-%d %H:%M:%S NY (trading)"),
                encoding="utf-8",
            )
        except Exception:
            pass
        d = self.day
        log.info("─" * 60)
        log.info("HEALTH @ %s | Bars=%d Patterns=%d Orders=%d/%d-fail PnL=$%.2f WSRecon=%d",
                 datetime.now(NY_TZ).strftime("%H:%M ET"),
                 d.bars_received, d.patterns_detected,
                 d.orders_submitted, d.orders_failed,
                 d.realized_pnl, d.ws_reconnects)
        # Tickers receiving bars?
        active = sum(1 for t in self.tickers.values() if len(t.bars) > 0)
        log.info("  Active tickers: %d/%d (got bars)", active, len(self.tickers))
        for sym, t in self.tickers.items():
            if t.in_position:
                log.info("  POSITION %s: %d shares @ $%.2f, stop $%.2f, T1 $%.2f, T2 $%.2f, half-filled=%s",
                         sym, t.shares, t.entry_price, t.stop_price,
                         t.target1_price, t.target2_price, t.half_filled)
        log.info("─" * 60)

    def _log_day_summary(self):
        d = self.day
        try:
            out = write_day_summary(d, d.spy_pct_today)
            log.info("Day summary saved: %s", out)
        except Exception as e:
            log.warning("day-summary write failed: %s", e)
        log.info("=" * 60)
        log.info("DAY SUMMARY")
        log.info("  Realized PnL:       $%.2f", d.realized_pnl)
        log.info("  Peak PnL:           $%.2f", d.peak_pnl)
        log.info("  Bars received:      %d", d.bars_received)
        log.info("  Patterns detected:  %d", d.patterns_detected)
        log.info("    rej MACD:         %d", d.patterns_rejected_macd)
        log.info("    rej FBO:          %d", d.patterns_rejected_fbo)
        log.info("    rej Pullback#3:   %d", d.patterns_rejected_pullback_count)
        log.info("    rej Size=0:       %d", d.patterns_rejected_size_zero)
        log.info("  Orders submitted:   %d (%d failed)", d.orders_submitted, d.orders_failed)
        log.info("  Consec losses:      %d (spiral=%s)", d.consecutive_losses, d.spiral_locked)
        log.info("  WS reconnects:      %d", d.ws_reconnects)
        log.info("=" * 60)

    async def handle_bar(self, bar):
        """Audit-Bug-Fix 2026-05-12 (Iteration 3): outer try/except.
        Eine Daten-Anomalie auf einem Symbol darf nicht den ganzen WS-Callback
        killen — alle anderen Symbole würden ihre Bar verlieren."""
        try:
            sym = bar.symbol
            if sym not in self.tickers: return
            ts = self.tickers[sym]
            bar_dict = {
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
                "timestamp": bar.timestamp,
            }
            ts.bars.append(bar_dict)
            try:
                ny_time = bar.timestamp.astimezone(timezone(timedelta(hours=-4))).time()
            except Exception:
                ny_time = datetime.now(NY_TZ).time()

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
            # Guard gegen unvollständige params
            required = ("pole_candles", "flag_candles", "pole_height", "entry_price", "stop_price")
            if not all(k in params for k in required):
                log.warning("PATTERN %s: incomplete params, skip", sym)
                return
            self.day.patterns_detected += 1
        except Exception as e:
            log.error("handle_bar(%s) crashed: %s", getattr(bar, "symbol", "?"), e, exc_info=True)
            return
        log.info("PATTERN %s: pole=%dx flag=%dx height=$%.2f → entry $%.2f stop $%.2f",
                 sym, params["pole_candles"], params["flag_candles"],
                 params["pole_height"], params["entry_price"], params["stop_price"])

        # Pullback-count check (3rd+ pullback skip)
        ts.pullback_count_today += 1
        if ts.pullback_count_today >= 3:
            self.day.patterns_rejected_pullback_count += 1
            log.info("  REJECT %s: 3rd+ pullback today (#%d)", sym, ts.pullback_count_today)
            return

        # Position-Size
        equity = self.executor.get_equity()
        shares = compute_position_size(params["entry_price"], params["stop_price"], equity, self.day)
        if shares < 1:
            self.day.patterns_rejected_size_zero += 1
            log.info("  REJECT %s: size=0 (entry $%.2f stop $%.2f risk-per-share $%.2f → max-shares 0)",
                     sym, params["entry_price"], params["stop_price"],
                     params["entry_price"] - params["stop_price"])
            return

        # #6 SPY-Size-Multiplier anwenden
        shares = int(shares * self.day.spy_size_multiplier)
        # Pump-Dump-Risiko: extremer Score → Position drastisch reduzieren (Fix 12.05)
        pd_mult = pd_size_multiplier(ts.score)
        if pd_mult < 1.0:
            shares = int(shares * pd_mult)
            log.warning("  PUMP-DUMP-RISK %s (score=%.0f) → size %.0fx", sym, ts.score, pd_mult)
        if shares < 1:
            self.day.patterns_rejected_size_zero += 1
            log.info("  REJECT %s: shares=0 nach SPY-multiplier %.2fx",
                     sym, self.day.spy_size_multiplier)
            return

        # Submit als BRACKET — Stop+TP broker-seitig, Position nie 'nackt'
        log.info("  SUBMITTING BRACKET-BUY %s %d shares  entry=$%.2f STOP=$%.2f TP2=$%.2f (rank=%d, spy_mult=%.1f)",
                 sym, shares, params["entry_price"], params["stop_price"],
                 params["target2"], ts.rank, self.day.spy_size_multiplier)
        order_id = self.executor.submit_bracket_buy(
            sym, shares, params["entry_price"],
            params["stop_price"], params["target2"],
        )
        if order_id:
            self.day.orders_submitted += 1
        else:
            self.day.orders_failed += 1
        if order_id:
            ts.in_position = True
            ts.entry_price = params["entry_price"]
            ts.entry_bar_idx = self.day.bars_received
            ts.bars_since_entry = 0
            ts.stop_price = params["stop_price"]
            ts.target1_price = params["target1"]
            ts.target2_price = params["target2"]
            ts.shares = shares
            ts.initial_shares = shares
            ts.adds_count = 0
            ts.last_add_price = params["entry_price"]
            ts.pole_candles = params["pole_candles"]
            ts.flag_candles = params["flag_candles"]
            ts.pole_height = params["pole_height"]
            ts.half_filled = False
            self.logger.log({
                "event": "entry", "symbol": sym, "rank": ts.rank, "score": ts.score,
                **params, "shares": shares, "order_id": order_id,
                "spy_mult": self.day.spy_size_multiplier,
            })

    async def manage_position(self, ts: TickerState, bar: dict, ny_time: dtime):
        ts.bars_since_entry += 1

        # Cameron MACD-Exit: bei bear-cross sofort raus (fade-away-Schutz)
        closes_now = [b["close"] for b in ts.bars]
        if len(closes_now) >= 30 and macd_bear_cross(closes_now):
            self.executor.submit_sell_limit(
                ts.symbol, ts.shares, bar["close"] - SLIPPAGE_CENTS, "macd_bear_cross"
            )
            # Audit-Iter 11 (2026-05-12) — Bug-Fix MP-1:
            # Nach T1-Partial fehlte hier die T1-Realisierung. Stop-Exit
            # hatte den Fix, MACD-Exit übersah die Gewinne der half-position.
            pnl = (bar["close"] - ts.entry_price) * ts.shares
            if ts.half_filled:
                pnl += (ts.target1_price - ts.entry_price) * (ts.initial_shares - ts.shares)
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.trades_completed_today += 1
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-DETECTION: 2 consecutive losses → STOP")
            else:
                # Audit-Bug-Fix 2026-05-12 (Iter 4): MACD-Win soll counter resetten
                self.day.consecutive_losses = 0
            self._check_daily_goal()  # MP-fix: war nur in T2/Stop-Exit
            self.logger.log({"event": "macd_exit", "symbol": ts.symbol,
                             "shares": ts.shares, "price": bar["close"], "pnl": pnl})
            log.info("  MACD-EXIT %s @ $%.2f (PnL $%.2f)", ts.symbol, bar["close"], pnl)
            ts.in_position = False
            return

        # #2 30¢-Quick-Exit: wenn 30c against entry und noch im Frühphase
        if not ts.half_filled and ts.bars_since_entry <= QUICK_EXIT_BARS_LIMIT:
            against = ts.entry_price - bar["close"]
            if against >= QUICK_EXIT_THRESHOLD_CENTS:
                # Stock läuft nicht in unsere Richtung — exit jetzt statt warten
                self.executor.submit_sell_limit(ts.symbol, ts.shares, bar["close"] - SLIPPAGE_CENTS, "quick_exit_30c")
                pnl = (bar["close"] - ts.entry_price) * ts.shares
                self.day.realized_pnl += pnl
                self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
                self.day.quick_exits += 1
                self.day.trades_completed_today += 1
                if pnl <= 0:
                    self.day.consecutive_losses += 1
                    if self.day.consecutive_losses >= 2:
                        self.day.spiral_locked = True
                        log.warning("SPIRAL-DETECTION: 2 consecutive losses → STOP")
                else:
                    # Quick-Exit-Win (rare) auch consecutive_losses resetten
                    self.day.consecutive_losses = 0
                self._check_daily_goal()  # MP-fix: war nur in T2/Stop-Exit
                self.logger.log({"event": "quick_exit", "symbol": ts.symbol,
                                 "shares": ts.shares, "price": bar["close"], "pnl": pnl})
                log.info("  QUICK-EXIT %s: -%.2fc against entry within %d bars (PnL $%.2f)",
                         ts.symbol, against * 100, ts.bars_since_entry, pnl)
                ts.in_position = False
                return

        # #1 Position-Adding (Pyramiding) auf Winners
        if ADD_TO_WINNER_ENABLED and ts.adds_count < MAX_ADDS_PER_TRADE:
            add_trigger_price = ts.last_add_price + ADD_TRIGGER_CENTS
            if bar["high"] >= add_trigger_price and bar["close"] > ts.entry_price:
                add_shares = max(1, int(ts.initial_shares * ADD_FRACTION))
                self.executor.submit_buy_limit(ts.symbol, add_shares, add_trigger_price)
                old_avg = ts.entry_price
                # neue Average-Cost-Basis
                ts.entry_price = (old_avg * ts.shares + add_trigger_price * add_shares) / (ts.shares + add_shares)
                ts.shares += add_shares
                ts.adds_count += 1
                ts.last_add_price = add_trigger_price
                self.day.adds_executed += 1
                self.logger.log({"event": "add", "symbol": ts.symbol, "shares": add_shares,
                                 "price": add_trigger_price, "new_avg": ts.entry_price,
                                 "total_shares": ts.shares, "adds": ts.adds_count})
                log.info("  ADD-TO-WINNER %s: +%d @ $%.2f → total %d, avg $%.2f (#%d)",
                         ts.symbol, add_shares, add_trigger_price, ts.shares,
                         ts.entry_price, ts.adds_count)
                # Move stop to BE on first add (Cameron-Rule)
                if ts.adds_count == 1:
                    ts.stop_price = old_avg
                # Add cancelt Bracket — neuer Schutz für komplette Restposition
                self.executor.protect_position(
                    ts.symbol, ts.shares, stop=ts.stop_price, take_profit=ts.target2_price,
                )
                return

        # T1 — Audit-Bug-Fix 2026-05-12 (Iter 4): mind. 2 Shares nötig für Partial
        if not ts.half_filled and bar["high"] >= ts.target1_price and ts.shares >= 2:
            half = ts.shares // 2
            self.executor.submit_sell_limit(ts.symbol, half, ts.target1_price, "T1_50pct")
            self.logger.log({"event": "T1", "symbol": ts.symbol, "shares": half, "price": ts.target1_price})
            ts.half_filled = True
            ts.shares -= half
            # Restposition braucht neue broker-seitige Schutzkette: Stop auf BE, TP=T2
            self.executor.protect_position(ts.symbol, ts.shares,
                                           stop=ts.entry_price, take_profit=ts.target2_price)
            self.day.cents_per_share_cumulative += (ts.target1_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
                log.info("Quarter-Size-Rule UNLOCKED today")
            return
        # T2 — Audit-Iter 4: 1-Share-Trades springen T1 → T2 direkt (kein half_filled)
        if bar["high"] >= ts.target2_price and ts.shares > 0:
            self.executor.submit_sell_limit(ts.symbol, ts.shares, ts.target2_price, "T2")
            if ts.half_filled:
                r1 = (ts.target1_price - ts.entry_price) * ts.initial_shares * 0.5
                r2 = (ts.target2_price - ts.entry_price) * ts.shares
            else:
                r1 = 0.0
                r2 = (ts.target2_price - ts.entry_price) * ts.shares
            pnl = r1 + r2
            self.logger.log({"event": "T2_exit", "symbol": ts.symbol, "shares": ts.shares,
                            "price": ts.target2_price, "pnl": pnl})
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.consecutive_losses = 0
            self.day.trades_completed_today += 1
            self._check_daily_goal()
            ts.in_position = False
            return
        # Stop / BE
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            self.executor.submit_sell_limit(ts.symbol, ts.shares, stop - SLIPPAGE_CENTS, "stop_or_BE")
            pnl = (stop - ts.entry_price) * ts.shares
            if ts.half_filled:
                pnl += (ts.target1_price - ts.entry_price) * (ts.initial_shares - ts.shares)
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            self.day.trades_completed_today += 1
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-DETECTION: 2 consecutive losses → trading stopped")
            else:
                self.day.consecutive_losses = 0
            self._check_daily_goal()
            self.logger.log({"event": "stop_exit", "symbol": ts.symbol, "shares": ts.shares,
                            "price": stop, "pnl": pnl, "reason": "stop" if not ts.half_filled else "BE"})
            ts.in_position = False
            return

    def _check_daily_goal(self):
        """#4 Daily-Goal-Stop."""
        if not self.day.goal_reached and self.day.realized_pnl >= DAILY_GOAL_USD:
            self.day.goal_reached = True
            log.warning("=" * 60)
            log.warning("DAILY GOAL $%.0f ERREICHT (PnL $%.2f) → KEINE NEUEN TRADES",
                        DAILY_GOAL_USD, self.day.realized_pnl)
            log.warning("=" * 60)


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


# ─── Daemon Mode (sleep until premarket, run one day, repeat) ──────────────
NY_TZ = timezone(timedelta(hours=-4))   # ET fixed (Mai = EDT)
PREMARKET_SCAN_TIME = dtime(6, 30)      # 06:30 ET = 12:30 CET


def next_premarket_start() -> datetime:
    """Returns time when bot must START premarket scan such that watchlist ready BY 06:30 ET.

    Premarket scan dauert ~3 Min, also 06:27 ET starten → 06:30 ready.
    Skips weekends.
    """
    ny_now = datetime.now(NY_TZ)
    target_finish = ny_now.replace(hour=6, minute=30, second=0, microsecond=0)
    if target_finish <= ny_now:
        target_finish += timedelta(days=1)
    # Skip weekends
    while target_finish.weekday() >= 5:
        target_finish += timedelta(days=1)
    # Subtract head-start so scan FINISHES at 06:30 ET
    return target_finish - timedelta(seconds=SCAN_HEAD_START_SLOW_SEC)


async def daemon_run(api_key: str, api_secret: str, dry_run: bool = False):
    """Endlosschleife: warte bis Premarket, traden, warte bis nächster Tag."""
    log.info("=" * 60)
    log.info("DAEMON MODE — runs until you Ctrl+C or PC sleeps")
    log.info("=" * 60)

    # Pre-Flight: verify auth, WS-init, yfinance — verhindert 2026-05-11-Geistermodus
    if not run_preflight(api_key, api_secret):
        log.error("Pre-Flight FAIL — daemon aborts (fix config + restart)")
        return

    # Position-Recovery: bei Crash/Restart mit offenen Positions → flatten.
    # Audit-Iter 6: return-value checken, bei FAILED nicht weiterstarten.
    try:
        from alpaca.trading.client import TradingClient
        _rc = recover_or_flatten(TradingClient(api_key, api_secret, paper=True))
        if _rc == -1:
            log.error("=" * 60)
            log.error("POSITION-RECOVERY FAILED — bot wartet 5min und versucht erneut")
            log.error("=" * 60)
            await asyncio.sleep(300)
            # Zweiter Versuch
            _rc = recover_or_flatten(TradingClient(api_key, api_secret, paper=True))
            if _rc == -1:
                log.error("RECOVERY-RETRY auch failed — daemon aborts (manuell prüfen!)")
                return
    except Exception as e:
        log.error("position-recovery raised: %s — daemon aborts", e, exc_info=True)
        return
    while True:
        # Mid-day-resume: wenn Restart während Trading-Fenster (06:27–HARD_FLAT) an Werktag → sofort traden statt morgen warten
        ny_now = datetime.now(NY_TZ)
        if ny_now.weekday() < 5 and dtime(6, 27) <= ny_now.time() < TIME_HARD_FLAT:
            log.info("MID-DAY-RESUME: Trading-Fenster offen → starte Session sofort (skip sleep)")
            try:
                bot = Bot(api_key, api_secret, dry_run=dry_run)
                await bot.run()
            except Exception as e:
                log.error("Trading day errored (resume): %s", e, exc_info=True)
            log.info("Resume-Session done. Looping.")
            continue
        next_start = next_premarket_start()
        ny_now = datetime.now(NY_TZ)
        wait_sec = (next_start - ny_now).total_seconds()
        wait_hours = wait_sec / 3600
        log.info("Next premarket-scan: %s ET (in %.1f h = %s CET)",
                 next_start.strftime("%Y-%m-%d %H:%M"),
                 wait_hours,
                 (next_start + timedelta(hours=6)).strftime("%H:%M"))
        log.info("Sleeping… heartbeat every 15 min, Ctrl+C to stop")

        # Sleep in 60-sec-chunks; heartbeat alle 15 Min
        last_heartbeat = datetime.now(NY_TZ)
        while datetime.now(NY_TZ) < next_start:
            try:
                await asyncio.sleep(60)
            except (KeyboardInterrupt, asyncio.CancelledError):
                log.info("Daemon stopped by user")
                return
            now = datetime.now(NY_TZ)
            if (now - last_heartbeat).total_seconds() >= 900:  # 15 Min
                remaining_h = (next_start - now).total_seconds() / 3600
                log.info("ALIVE — sleeping. Next scan in %.1f h at %s CET",
                         remaining_h,
                         (next_start + timedelta(hours=6)).strftime("%H:%M"))
                last_heartbeat = now
            # Heartbeat-File für externe Watchdogs (jede 60s aktualisiert)
            try:
                hb_file = Path(__file__).parent / "heartbeat.txt"
                hb_file.write_text(now.strftime("%Y-%m-%d %H:%M:%S NY"), encoding="utf-8")
            except Exception:
                pass

        log.info("=" * 60)
        log.info("PREMARKET TIME — starting one trading day")
        log.info("=" * 60)
        try:
            bot = Bot(api_key, api_secret, dry_run=dry_run)
            await bot.run()
        except Exception as e:
            log.error("Trading day errored: %s — sleeping until next day", e, exc_info=True)
        log.info("Trading day done. Looping for next session.")


# ─── CLI ────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Pattern-Detection only, no orders")
    p.add_argument("--scan-only", action="store_true", help="Premarket-Scan + exit")
    p.add_argument("--replay", type=str, help="Historical replay YYYY-MM-DD aus pilot-data")
    p.add_argument("--check-connection", action="store_true", help="Alpaca-Auth verifizieren")
    p.add_argument("--status", action="store_true", help="Account + Positions anzeigen")
    p.add_argument("--daemon", action="store_true", help="Endlos-Modus: warte auf nächste Session, tradeen, repeat")
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

    try:
        from secrets_loader import get_alpaca_keys
        api_key, api_secret = get_alpaca_keys()
    except Exception:
        api_key = ""
        api_secret = ""
    if not api_key or not api_secret:
        log.error("Set APCA_API_KEY_ID + APCA_API_SECRET_KEY env-vars first.")
        log.error("Or use --replay YYYY-MM-DD for offline-test")
        log.error("Or use --scan-only for pure scanner test")
        log.error("Alpaca paper signup: https://app.alpaca.markets/signup")
        return

    if args.daemon:
        asyncio.run(daemon_run(api_key, api_secret, dry_run=args.dry_run))
        return

    bot = Bot(api_key, api_secret, dry_run=args.dry_run)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
