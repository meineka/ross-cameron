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

# Phase-31: patch alpaca-py DataStream._run_forever so connection-limit-
# exceeded errors hit a real backoff instead of hammering Alpaca at 1.6Hz.
# Idempotent — safe to call on every import (e.g. test collection).
# Phase-43: also enable the StockDataStream singleton at LIVE bot startup
# only (NOT auto on import). Tests that just import bot.py get the
# backoff patch but NOT the singleton, so per-test SDK behavior is
# preserved. _enable_ws_singleton is called from Bot.run() — see below.
try:
    from alpaca_ws_patch import install_patch as _install_alpaca_ws_patch
    from alpaca_ws_patch import enable_ws_singleton as _enable_ws_singleton
    _install_alpaca_ws_patch()
except Exception as _e:
    logging.getLogger(__name__).warning("alpaca_ws_patch install failed: %s", _e)
    _enable_ws_singleton = None

# Phase-53 (2026-05-15, ChatGPT-review P0): every TradingClient and
# StockHistoricalDataClient instantiation now routes through the
# guarded wrappers (process-global RateGuard + alpaca_api_calls.jsonl).
# Drop-in replacement — same constructor signature.
try:
    from guarded_alpaca import (
        GuardedTradingClient as _GuardedTC,
        GuardedStockHistoricalDataClient as _GuardedDC,
    )
except Exception as _e:
    logging.getLogger(__name__).warning("guarded_alpaca import failed: %s", _e)
    _GuardedTC = TradingClient   # fallback to raw client
    _GuardedDC = StockHistoricalDataClient

# Lokale Module für Verbesserungen
sys.path.insert(0, str(Path(__file__).parent))
from pre_flight import run_preflight
from watchlist_persist import save_watchlist, load_watchlist_if_fresh, load_watchlist_with_scores
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

# Phase-78 (2026-05-19): rotation on bot.log. Old plain FileHandler grew
# to 15.3 MB / 194K lines / 10 days unchecked. RotatingFileHandler caps
# at 5 MB per file × 5 backups = max 30 MB on disk. Operator's tail of
# bot.log always shows recent activity; older windows are bot.log.1..5.
from logging.handlers import RotatingFileHandler as _RotatingFileHandler
_BOT_LOG_PATH = Path(__file__).parent / "bot.log"
_bot_file_handler = _RotatingFileHandler(
    _BOT_LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_bot_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)s [%(name)s] %(message)s"
))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _bot_file_handler,
    ],
)
log = logging.getLogger("bot")
# yfinance-Spam ein-dämmen — delisted-Symbol-ERRORs sind kein Problem,
# sie haben heute 1000+ Logs/Audit-Alarme verursacht.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Phase-66/69: STRATEGY_VARIANT init must come BEFORE Cameron-Constants
# because the `loose` variant overrides some of them.
import os as _os_phase66

# Phase-66.1 load .env so STRATEGY_VARIANT can be pinned in 06_live_bot/.env
try:
    from secrets_loader import _load_env_file as _phase66_load_env
    _phase66_load_env()
except Exception:
    pass

# Phase-69: add "loose" variant for emergency "no trade today" sessions
# Phase-72: add "ultra" variant — looser than loose + skips entry vetos
# Phase-79: add "force" variant — bypasses pattern detector entirely,
#           paper-only stress test ("alle constraints weg, alle 5 min").
STRATEGY_VARIANT = _os_phase66.environ.get("STRATEGY_VARIANT", "strict").lower()
if STRATEGY_VARIANT not in ("strict", "relaxed", "loose", "ultra", "force"):
    STRATEGY_VARIANT = "strict"  # safe default

# ─── Cameron-Constants (mirror constraints.yaml) ────────────────────────────
# Phase-69: PRICE/FLOAT range stays same in all variants — Cameron only
# trades small-cap penny-to-low-dollar. Only DAILY_GAIN, RVOL and the
# pattern-detection thresholds loosen in `loose` variant.
PRICE_MIN, PRICE_MAX = 2.0, 20.0  # Phase-51 revert: Cameron-strict (2-20)
# DAILY_GAIN + RVOL initialized to strict here; overridden below for `loose`.
DAILY_GAIN_MIN_PCT = 10.0  # Phase-51 revert: Cameron-strict (≥10% daily gain)
RVOL_MIN_PROXY = 5.0  # Phase-51 revert: Cameron-strict (≥5x relative volume)
FLOAT_MAX_SHARES = 10_000_000  # Phase-51 revert: Cameron-strict (<10M float)

# ── Phase-35: Alpaca rate-limits + stall-probe behavior ──────────────────
# These are the single source of truth for Alpaca's documented account
# limits. Override in alpaca_rate_guard.py if you need different
# behavior — bot.py just imports + exposes them here for visibility
# from the constants section.
from alpaca_rate_guard import (
    ALPACA_MAX_CALLS_PER_MIN,
    ALPACA_STALL_PROBE_INTERVAL_SEC,
    ALPACA_STALL_AFTER_N_FAILS,
)  # noqa: E402, F401  — explicit re-export for operator visibility
CATALYST_REQUIRED = True  # 5. Cameron-Pillar
# Review-V2 P1.3: catalyst-filter mode. "soft" (default) passes on
# data-source issues (empty news, yfinance error) — preserves V1 behavior
# and tolerates yfinance off-hours rate-limits. "strict" fails-closed —
# unknown catalyst means no trade. "off" disables filter entirely.
# For live trading with real money, set CATALYST_MODE="strict".
CATALYST_MODE = "soft"
POWER_HOUR_END = dtime(10, 30)
# Trader-loop Iter 24 (2026-05-14): swap Power-Hour vs Post-Power.
# Pilot diagnosis: 9:30-10:30 = 75% WR (volatile chop), 10:30+ = 100% WR
# (clean mid-morning setups). Original 1.0/0.75 sized UP during chop —
# completely backwards. Swap: smaller in volatile open, full size when
# pattern-clean.
# 42-day pilot: PnL $329→$391 (+19%), MDD -$7.20→-$5.40, Sharpe 45.83→72.43.
POWER_HOUR_SIZE_MULT = 0.75
POST_POWER_SIZE_MULT = 1.0
TOP_N = 10
TIMEFRAME = "5Min"

# Bar-Aggregation: Alpaca-WS liefert 1-Min Bars; Cameron tradet 5-Min charts.
# Pattern + Pole/Flag-Thresholds sind für 5-Min kalibriert.
# Phase-51 revert from user's Phase-36 1m experiment back to Cameron-strict.
BAR_AGGREGATION_MINUTES = 5

# Phase-51 (2026-05-15): reverted from Phase-33 user-override
# ("see-some-trades mode") back to Cameron-strict backtest-optimum.
# Phase-33 looser values preserved in code-comments for one-line
# rollback if user wants the demo mode again.
POLE_MIN_CANDLES, POLE_MAX_CANDLES = 3, 7  # Cameron-strict (was Phase-33 2,7)
# Cameron-strict POLE_MIN_MOVE_PCT = 4.0 (backtest-optimum on 167d pilot).
POLE_MIN_MOVE_PCT = 4.0  # Cameron-strict (was Phase-33 2.5)
# Cameron-spec 0.5 = "topping-tail > 50% of range vetoes pole".
POLE_TOPPING_TAIL_MAX = 0.5  # Cameron-strict (was Phase-33 0.7)
FLAG_MIN_CANDLES, FLAG_MAX_CANDLES = 1, 3  # Cameron-strict (was Phase-33 1,4)
FLAG_RETRACE_MAX_PCT = 50.0  # Cameron-strict (was Phase-33 70.0)
BREAKOUT_VOL_FACTOR = 1.5  # Cameron-strict (was Phase-33 1.2)
SLIPPAGE_CENTS = 0.01

# Phase-69 (loose-mode override): if STRATEGY_VARIANT=loose, swap the
# strict thresholds for the Phase-33 "see-some-trades" values that
# actually produce entries on quiet days. Emergency mode — pilot
# backtest showed these have lower edge but more trades.
# Phase-72: "ultra" overlay applies ON TOP of loose values.
DISABLE_ENTRY_VETOS = False  # phase-72 flag; ultra sets True
if STRATEGY_VARIANT in ("loose", "ultra"):
    DAILY_GAIN_MIN_PCT = 5.0   # loose-algo: was strict 10.0
    RVOL_MIN_PROXY = 3.0       # loose-algo: was strict 5.0
    POLE_MIN_CANDLES, POLE_MAX_CANDLES = 2, 10  # loose-algo: was 3,7
    POLE_MIN_MOVE_PCT = 2.5    # loose-algo: was 4.0
    POLE_TOPPING_TAIL_MAX = 0.7  # loose-algo: was 0.5
    FLAG_MIN_CANDLES, FLAG_MAX_CANDLES = 1, 4  # loose-algo: was 1,3
    FLAG_RETRACE_MAX_PCT = 70.0  # loose-algo: was 50.0
    BREAKOUT_VOL_FACTOR = 1.2    # loose-algo: was 1.5
    CATALYST_MODE = "off"         # loose-algo: no 8-K filter (was "soft")

if STRATEGY_VARIANT == "ultra":
    # ultra-algo (Phase-72): even looser than loose. Goal: produce a
    # trade even on quiet days for end-to-end execution validation.
    # NOT for live money.
    DAILY_GAIN_MIN_PCT = 3.0   # ultra-algo: tiny premarket move OK
    RVOL_MIN_PROXY = 2.0       # ultra-algo: 2x volume is enough
    POLE_MIN_CANDLES, POLE_MAX_CANDLES = 1, 15  # ultra-algo: huge window
    POLE_MIN_MOVE_PCT = 1.0    # ultra-algo: 1% pole accepted
    POLE_TOPPING_TAIL_MAX = 0.9  # ultra-algo: nearly disabled
    FLAG_MIN_CANDLES, FLAG_MAX_CANDLES = 1, 8  # ultra-algo: long flag OK
    FLAG_RETRACE_MAX_PCT = 90.0  # ultra-algo: deep retraces OK
    BREAKOUT_VOL_FACTOR = 1.0    # ultra-algo: any breakout volume
    DISABLE_ENTRY_VETOS = True   # ultra-algo: skip VWAP/MACD/FBO checks

# Phase-79 (2026-05-19, user request "mach mal alle constraints weg dass
# er free traden kann und lass ihn alle 5 minuten was prüfen"): bypass
# the pattern detector entirely. Every 5-min bar check forces a synthetic
# BUY signal at the current close with tight 1% stop / 2% target.
#
# This is a PAPER-ONLY end-to-end stress test of the trade-execution
# path — NOT a strategy. Position size is tiny (fixed-shares) to keep
# from burning the paper $100k on garbage. The detector / vetos / 3rd-
# pullback / pump-dump filter are ALL bypassed when the "force" variant
# is active.
FORCE_ENTRY_ON_BAR = False  # phase-79 flag; force-variant sets True
if STRATEGY_VARIANT == "force":
    # force-algo (Phase-79): unconditional entry on each 5-min bar.
    # Inherits ultra-loose thresholds (in case detector still gets
    # called for any reason) but the real entry path is the synthetic
    # signal in handle_bar_5min.
    DAILY_GAIN_MIN_PCT = 0.0   # force-algo: scan keeps anything
    RVOL_MIN_PROXY = 1.0       # force-algo: any RVOL
    POLE_MIN_CANDLES, POLE_MAX_CANDLES = 1, 30  # force-algo
    POLE_MIN_MOVE_PCT = 0.0    # force-algo: any pole shape
    POLE_TOPPING_TAIL_MAX = 1.0  # force-algo: ignored
    FLAG_MIN_CANDLES, FLAG_MAX_CANDLES = 1, 30  # force-algo
    FLAG_RETRACE_MAX_PCT = 100.0
    BREAKOUT_VOL_FACTOR = 0.0    # force-algo: any volume
    DISABLE_ENTRY_VETOS = True
    FORCE_ENTRY_ON_BAR = True

# Phase-66 (2026-05-17): two-variant strategy support.
#
# STRATEGY_VARIANT env var picks the position-sizing risk profile:
#
#   "strict"  (default, conservative)  — original Cameron-strict sizing
#                                          MAX_LOSS_PER_TRADE = $50
#                                          equity-cap = 1% of account
#                                          DAILY_MAX_LOSS = $150 = 3× per-trade
#
#   "relaxed" (2× volume)               — doubled position sizes
#                                          MAX_LOSS_PER_TRADE = $100
#                                          equity-cap = 2% of account
#                                          DAILY_MAX_LOSS = $300 = 3× per-trade
#
# Same Cameron-strict ENTRY criteria (pole/flag/RVOL/float etc) for both;
# only the position size differs. Use case: A/B compare profit vs drawdown
# of equal-edge entries at two risk levels.
#
# To switch variants:
#   STRATEGY_VARIANT=relaxed python bot.py --daemon
#
# Code is marked with `# strict-algo` and `# relaxed-algo` annotations so
# a reader can immediately see which path produces which value.

# Phase-66/69: STRATEGY_VARIANT is initialized ABOVE the Cameron-Constants
# (see top of module). Here we just use it to pick the position-size
# envelope. Entry-threshold overrides for `loose` live with the
# constants block above.

if STRATEGY_VARIANT == "relaxed":
    # relaxed-algo (Phase-66): 2× position-size envelope, strict entries
    MAX_LOSS_PER_TRADE_USD = 100.0     # relaxed-algo: 2× of strict $50
    DAILY_MAX_LOSS_USD = 300.0          # relaxed-algo: keeps 3× ratio
    DAILY_GOAL_USD = 300.0              # relaxed-algo: symmetric
    EQUITY_RISK_CAP_PCT = 2.0           # relaxed-algo: 2% (vs strict 1%)
elif STRATEGY_VARIANT == "loose":
    # loose-algo (Phase-69): 2× sizing AND loosened entry criteria.
    # Emergency mode for sessions where strict thresholds reject every
    # candidate. Same envelope as relaxed; entry thresholds are
    # explicitly opened below (POLE_*, RVOL, gain, etc).
    MAX_LOSS_PER_TRADE_USD = 100.0     # loose-algo: 2× of strict
    DAILY_MAX_LOSS_USD = 300.0          # loose-algo
    DAILY_GOAL_USD = 300.0              # loose-algo
    EQUITY_RISK_CAP_PCT = 2.0           # loose-algo
elif STRATEGY_VARIANT == "ultra":
    # ultra-algo (Phase-72): MAXIMUM looseness. Same sizing as loose,
    # plus EVEN LOOSER entry thresholds (pole 1%, breakout 1.0x,
    # retrace 90%) AND skip the VWAP/MACD/FBO entry-vetos entirely.
    # User-request 2026-05-18: "hätte gerne dass er sehr locker heute
    # noch trades machen kann" — when loose still produces zero trades.
    # NOT for live money — pure demo / Paper end-to-end validation.
    MAX_LOSS_PER_TRADE_USD = 100.0     # ultra-algo: same as loose
    DAILY_MAX_LOSS_USD = 300.0          # ultra-algo
    DAILY_GOAL_USD = 300.0              # ultra-algo
    EQUITY_RISK_CAP_PCT = 2.0           # ultra-algo
elif STRATEGY_VARIANT == "force":
    # force-algo (Phase-79): PAPER-ONLY stress test. Position size is
    # tiny ($20 max loss/trade) so 100 garbage trades = $2000 max loss
    # — survivable on $100k paper. Daily cap stays at $300 to fail-stop
    # if force-mode becomes chronically losing.
    MAX_LOSS_PER_TRADE_USD = 20.0      # force-algo: small bets
    DAILY_MAX_LOSS_USD = 300.0          # force-algo: same fail-stop
    DAILY_GOAL_USD = 300.0              # force-algo
    EQUITY_RISK_CAP_PCT = 0.5           # force-algo: half of strict (tiny shares)
else:
    # strict-algo (default Cameron-strict): conservative paper-mode sizing
    MAX_LOSS_PER_TRADE_USD = 50.0      # strict-algo
    DAILY_MAX_LOSS_USD = 150.0          # strict-algo: 3× per-trade
    DAILY_GOAL_USD = 150.0              # strict-algo: symmetric (Cameron-Rule)
    EQUITY_RISK_CAP_PCT = 1.0           # strict-algo: Cameron's 1%
INTRADAY_DRAWDOWN_PCT_OF_PROFITS = 50.0  # same for both

LIQUIDITY_CAP_PCT_OF_AVG_VOL = 1.0
# Review-fix 2026-05-13 (Reviewer #12): spec sagt "$0.50/share cumulative
# gain before unlocking full size". Vorher 0.20 (zu früh full size).
# Name geändert zu USD_PER_SHARE — "CENTS" war irreführend.
QUARTER_SIZE_UNLOCK_USD_PER_SHARE = 0.50
QUARTER_SIZE_UNLOCK_CENTS = QUARTER_SIZE_UNLOCK_USD_PER_SHARE  # backwards-compat alias
# Trader-loop Iter 23 (2026-05-14): Cameron's actual rule is "quarter-size
# DURING volatile open, full size after". Bot's cents-based unlock never
# fires on 1-trade-days (most days). Time-based fallback unlocks at 10:00 NY
# regardless. 42-day pilot: PnL $164→$413 (+150%), MDD unchanged, Sharpe
# 22.89→57.41 (+150%). ANNA-loss stays quarter-size (entry pre-10:00),
# all wins after 10:00 → full-size.
from datetime import time as _dtime_qsu
QUARTER_SIZE_TIME_UNLOCK = _dtime_qsu(10, 0)

# ── 8 Easy-Wins (Cameron-Compliance) ───────────────────────────────────────
# #4 Daily-Goal-Stop: bei erreichen STOP
DAILY_GOAL_STOP_ENABLED = True

# #5 Max-Trades pro Tag (Cameron-Rule: Quality > Quantity)
MAX_TRADES_PER_DAY = 5             # Cameron sagt 1 für Beginners, 3-5 für ihn selbst

# Cameron-rule: "tight stops only — if stop is more than 8-10% away, pass"
# Trader-loop Iter 1 (2026-05-13): pilot-backtest zeigt
#   - Trades mit risk%>=10%: 5/5 → win-rate 20%
#   - Trades mit risk%<10%:  12/12 → win-rate 92%
# MAX_RISK_PCT=8% Filter ergibt: 9 trades (vs 17), $73 PnL (vs $75 — gleich),
# Win-Rate 78% (vs 67%), MaxDD -$18.78 (vs -$30.63 halbiert), 0 Spirals.
# Sharpe-like-Ratio +59%.
MAX_RISK_PCT = 5.0  # Phase-51 revert: Cameron-strict (was Phase-33 7.0).
# Iter 36 (2026-05-14): pilot extended to 167d revealed
# 5.5% Sharpe collapses in bad months (Sept 2025: 3-loss cluster).
# Sharpe-stability across pilots favors 5.0% (range 6-17 vs 5.5% 6-21).
# 167d sweep:
#   5.0/3.5R: 17 trd / $582 / 81% / MDD -$50 / Sharpe 11.58 ← selected
#   5.5/3.5R: 20 trd / $669 / 79% / MDD -$100 / Sharpe 6.69 (Iter 29 selection)
# Trade-off: -$87 PnL for halved MDD + 73% better Sharpe. Worth it for live.
# Iter 32 originally refused this cascade — but 167d makes the 5.0% signal
# robust (5 pilots show consistent improvement at 5.0%).

# Trader-loop Iter 7 (2026-05-14): MAX_POLE_T2_R-Cap (Cameron "don't chase
# overextended"). Pole_height-based T2 erlaubte unbegrenzt große Poles —
# Pilot-Diagnose zeigt alle 3 Verluste (FGI/ANNA/MSC) hatten t2R >= 2.4,
# große Poles = volatile/exhausted Stocks. Cap t2R <= 3.5R eliminiert
# nur 2 Trades (FGI, MSC — beide LOSSES): 13→11 trades, +$120→+$145 PnL,
# 75%→90% win-rate, MaxDD identisch, Sharpe-like +21%.
MAX_POLE_T2_R = 3.5

# Trader-loop Iter 25 (2026-05-14): T2 as R-multiple instead of pole_height.
# Cameron's classic teaching: "2.5x reward-to-risk minimum on T2".
# 42-day pilot with Iter 23+24 active: T2=2.5R gives $461.82 vs pole-based
# $391.13 (+18% PnL), Sharpe 72.43→85.52, MDD unchanged. Iter 3c originally
# tested at 39-day pilot (+12%) — confirmed signal on larger sample.
T2_R_MULTIPLE = 3.5  # Iter 30 (2026-05-14): pilot extended to 102d.
# T2 sweep on 102d clear peak at 3.5R:
#   2.5R: $563 / 15.19 (was Iter 25 selection on 42d)
#   3.0R: $566 / 15.26
#   3.5R: $632 / 17.05  ← selected
#   4.0R: $550 / 14.84 (2 trades fail to reach T2, get BE-stopped)
# Same trades/WR/MDD at 3.5R, just bigger wins. Momentum carries past 2.5R
# but not past 4R. Cameron "let your winners run" maxim — fits stronger
# setups in the data.

# Quick-Exit ("take the quick loss"): wenn N¢ gegen Entry → exit.
# Trader-Loop Iter 5 (2026-05-14): 167-day pilot sweep showed 20c is
# tighter than the legacy 30c without losing any winners — clips losers
# earlier. Net effect (vs 30c):
#   PnL  $778.60 -> $793.90   (+$15)
#   Trd  19      -> 19         same
#   WR   83%     -> 83%        same
#   DD   -$50.25 -> -$37.10    -26%
#   Shp  15.49   -> 21.40      +38%
# Cameron's literal rule is "30c quick-exit" but 20c is more consistent
# with his actual behavior on lower-priced ($2-$10) tickets which dominate
# the bot's universe (PRICE_MIN/MAX = 2-20).
QUICK_EXIT_THRESHOLD_CENTS = 0.20
QUICK_EXIT_BARS_LIMIT = 5          # innerhalb 5 Bars nach Entry

# #1 Position-Adding (Pyramiding): bei jedem +10¢ höher 25% mehr Shares (max 3 Adds)
ADD_TO_WINNER_ENABLED = True
ADD_TRIGGER_CENTS = 0.10           # alle 10¢ above entry
ADD_FRACTION = 0.25                # 25% extra shares pro Add
MAX_ADDS_PER_TRADE = 3

# #8 Slippage realistisch
SLIPPAGE_CENTS = 0.05              # was 0.01, jetzt realistic 5c

# #6 SPY-Trend-Filter: skip Trading wenn SPY < -1% am Tag (Bear-Day)
# Trader-loop Iter 22 (2026-05-14): SPY-Veto -1.0% war zu strict.
# 42-day pilot: 4 days skipped, $40 PnL verloren, Sharpe -25%.
# Cameron-Praxis: "trade with caution on red days" = size-reduce, NICHT
# skip. -0.5% Reduce-Logic bleibt, Outright-Skip nur bei echtem Crash.
SPY_TREND_VETO_PCT = -2.0
SPY_TREND_REDUCE_SIZE_PCT = -0.5   # SPY < -0.5% aber > -2%: size 50%

# #3 Whole/Half-Dollar Targets
USE_PSYCH_LEVEL_T2 = True          # T2 = max(pole_height_target, nearest psych level)

# #7 Pole-Volume-Rising
POLE_VOLUME_RISING_REQUIRED = True

# Time-Cuts (NY-Time)
TIME_NEW_ENTRIES_END = dtime(11, 30)
TIME_HARD_FLAT = dtime(12, 0)
TIME_RTH_START = dtime(9, 30)
TIME_NEW_ENTRIES_START = dtime(9, 35)

# Phase-70 (2026-05-18 user-request): operator override for HARD_FLAT.
# Cameron's strict rule is "stop trading at 12:00 NY" — captures the
# morning-session edge and avoids afternoon chop. But for sessions where
# the operator wants the bot to keep trading until market close (e.g.
# loose-mode exploration runs), SKIP_HARD_FLAT_TODAY=1 in .env / shell
# env pushes the cut-off to TIME_NEW_ENTRIES_END_OVERRIDE (default 15:55
# NY, 5min before close) and TIME_HARD_FLAT_OVERRIDE (16:00 NY).
#
# This is NOT for live money — afternoon trading is HISTORICALLY worse
# in Cameron-strategy backtests (afternoon chop vs morning trend).
# Use only for end-to-end execution validation on quiet days.
SKIP_HARD_FLAT_TODAY = _os_phase66.environ.get(
    "SKIP_HARD_FLAT_TODAY", "0"
).strip().lower() in ("1", "true", "yes", "on")
if SKIP_HARD_FLAT_TODAY:
    TIME_NEW_ENTRIES_END = dtime(15, 30)  # phase-70: 3:30pm NY new entries cut
    TIME_HARD_FLAT = dtime(15, 55)         # phase-70: 3:55pm NY hard flat (5min pre-close)

# Re-Scan-Strategie: zwei Schichten, ALIGNED zu round-5-Min boundaries
# SLOW: yfinance Universe-Pull, ~3 Min Laufzeit → 180 Sek Head-Start
# FAST: Alpaca Snapshot, <1 Sek → 5 Sek Head-Start
SCAN_HEAD_START_SLOW_SEC = 90   # 2026-05-15 Phase-32: 180→90 — must be < SLOW_INTERVAL*60
SCAN_HEAD_START_FAST_SEC = 5    # Alpaca snapshot dauer
RESCAN_SLOW_INTERVAL_MIN = 2    # 2026-05-15 user-override: 5 → 2 (TV-scan every 2 min)
RESCAN_FAST_INTERVAL_MIN = 1    # alle 1 Min Alpaca-Re-Rank
RESCAN_FAST_PHASE_END = dtime(10, 30)  # Power-Hour Ende


def aligned_scan_start(now: datetime, period_min: int, head_start_sec: int) -> datetime:
    """Returns next datetime where scan must START to FINISH at next round boundary.

    Beispiel: period_min=5, head_start=180 → finish bei :00, :05, :10, :15, ...
    Start bei :02:00, :07:00, :12:00, :17:00, :22:00, :27:00, :32:00 ...

    Phase-32 (2026-05-15): when head_start_sec >= period_min*60 the old
    math produced a start-time IN THE PAST, causing the rescan to fire
    on every loop iteration (~3s) → cascading WS resubscribes →
    Alpaca connection-limit-exceeded. Now we clamp head_start to at
    most period-30s and advance forward until start > now.
    """
    max_head_start = max(0, period_min * 60 - 30)
    head_start_sec = min(head_start_sec, max_head_start)
    minutes_past = now.minute % period_min
    next_boundary = now.replace(second=0, microsecond=0) + timedelta(
        minutes=period_min - minutes_past)
    start = next_boundary - timedelta(seconds=head_start_sec)
    while start <= now:
        next_boundary = next_boundary + timedelta(minutes=period_min)
        start = next_boundary - timedelta(seconds=head_start_sec)
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
    t1_shares_sold: int = 0         # tatsächlich am T1 verkaufte Shares
                                    # (Audit-Iter 12: bei Pyramiding ≠ initial*0.5)
    intraday_pct: float = 0.0       # Audit-Iter 22: für pd_size_multiplier
    rvol_proxy: float = 0.0         # Audit-Iter 22: für pd_size_multiplier
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
    patterns_rejected_vwap: int = 0          # Review-V2 P1.8
    patterns_rejected_risk: int = 0          # Review-V2 P1.8 (MAX_RISK_PCT)
    patterns_rejected_pole_extension: int = 0  # Review-V2 P1.8 (MAX_POLE_T2_R)
    patterns_rejected_risk_budget: int = 0   # Review-V2 P0.5
    patterns_rejected_quote_safety: int = 0  # Review-V2 P0.4 (safe_bracket wiring)
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
    # Phase-60 (ChatGPT P1 follow-up): expose the most recent "why no
    # entry?" reason via status.json so operators can answer the
    # no-trade question in one glance. Updated by pattern-rejection
    # paths in manage_position + scan logic.
    last_no_trade_reason: str | None = None
    last_ws_bar_ts: str | None = None
    last_tradingview_scan_status: str | None = None
    scanner_source: str | None = None
    fallback_used: bool = False
    alpaca_blocked_count: int = 0


# ─── Premarket-Scanner ──────────────────────────────────────────────────────
_UNIVERSE_CACHE_FILE = Path(__file__).parent / "universe_cache.json"
_UNIVERSE_CACHE_TTL_SEC = 4 * 3600  # 4h


def _load_cached_universe() -> tuple[list[str] | None, float | None]:
    """Returns (tickers, age_seconds) or (None, None) if no/invalid cache."""
    if not _UNIVERSE_CACHE_FILE.exists():
        return None, None
    try:
        import time as _t
        data = json.loads(_UNIVERSE_CACHE_FILE.read_text(encoding="utf-8"))
        tickers = data.get("tickers")
        ts = data.get("ts")
        if not isinstance(tickers, list) or not isinstance(ts, (int, float)):
            return None, None
        return tickers, _t.time() - ts
    except (OSError, json.JSONDecodeError, ValueError):
        return None, None


def _save_cached_universe(tickers: list[str]) -> None:
    """Atomic write (tmp + rename) damit crash mid-write keine corrupt JSON."""
    import time as _t
    tmp = _UNIVERSE_CACHE_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps({"ts": _t.time(), "tickers": tickers}),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(_UNIVERSE_CACHE_FILE))
    except OSError as e:
        log.warning("universe cache save failed: %s", e)
        try: tmp.unlink(missing_ok=True)
        except Exception: pass


def fetch_us_universe(use_cache: bool = True, max_retries: int = 2) -> list[str]:
    """NASDAQ-Trader: alle US-Tickers (nasdaqlisted + otherlisted).

    Audit-Iter 25 (2026-05-12) — Bug-Fixes UV-2/UV-4/UV-9:
      - in-memory + disk-Cache mit 4h TTL (UV-4)
      - retry mit backoff per URL (UV-2)
      - stale-cache-fallback wenn beide URLs failen (UV-9)
      - User-Agent header (UV-8)
    """
    import time as _t
    # 1. Fresh-Cache-Hit?
    if use_cache:
        cached, age = _load_cached_universe()
        if cached is not None and age is not None and age < _UNIVERSE_CACHE_TTL_SEC:
            log.info("universe: cache hit (age %.0fmin, %d tickers)",
                     age/60, len(cached))
            return cached
    import requests, io as _io
    urls = [
        "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
        "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
    ]
    headers = {"User-Agent": "Cameron-Bot/1.0 (paper-trading)"}
    tickers: set[str] = set()
    for u in urls:
        for attempt in range(max_retries + 1):
            try:
                r = requests.get(u, timeout=20, headers=headers)
                r.raise_for_status()
                if not r.text.strip():
                    raise ValueError("empty response")
                df = pd.read_csv(_io.StringIO(r.text), sep="|")
                col = "Symbol" if "Symbol" in df.columns else "ACT Symbol"
                df = df[df.get("Test Issue", "N") == "N"]
                if "ETF" in df.columns:
                    df = df[df["ETF"] == "N"]
                tickers.update(df[col].dropna().astype(str).tolist())
                break  # success
            except Exception as e:
                log.warning("universe fetch %s attempt %d/%d: %s",
                            u, attempt + 1, max_retries + 1, e)
                if attempt < max_retries:
                    _t.sleep(2 * (attempt + 1))
    tickers = {t for t in tickers if t.isalpha() and 1 <= len(t) <= 5}
    # 2. Cache-Fallback wenn alle URLs failed (UV-9)
    if not tickers and use_cache:
        cached, age = _load_cached_universe()
        if cached is not None:
            log.warning("universe: ALL URLs failed — fallback to stale cache "
                        "(age %.0fmin, %d tickers)", (age or 0)/60, len(cached))
            return cached
    result = sorted(tickers)
    # 3. Cache speichern für nächste Calls + zukünftigen Fallback
    if result and use_cache:
        _save_cached_universe(result)
    return result


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


# Phase-73 (ChatGPT 20260518_2040 P1): module-level state that TV-scan
# writes on every call so the caller can surface it via day.scanner_source
# / day.last_tradingview_scan_status / day.fallback_used. Avoids the
# alternative refactor of plumbing `day` through the scan call chain.
_LAST_TV_SCAN_STATE: dict = {
    "status": None,        # "ok" | "error" | "import_error" | None
    "result_count": 0,
    "error_class": None,
    "ts": None,
}


def _try_tradingview_primary(top_n: int) -> list[dict]:
    """Phase-28: ask TradingView for top-N Cameron-conformant candidates.
    Returns [] on any error so caller can fall back to legacy yfinance path.
    Never raises.

    Phase-62 (ChatGPT 1817 ask): every TradingView call is logged to
    market_data_calls.jsonl with `source="tradingview"`, `status`,
    `latency_ms`, `result_count` so postmortem can distinguish
    "TV-empty" (status=ok n=0), "TV-error" (status=error), and
    "TV-unavailable" (status=import_error) without grepping bot.log.
    """
    import time as _t
    from datetime import datetime as _dt, timezone as _tz
    t_start = _t.monotonic()
    try:
        from scanners.tradingview_scanner import scan_cameron_candidates
    except ImportError as e:
        latency_ms = (_t.monotonic() - t_start) * 1000
        _log_tv_scan(status="import_error", latency_ms=latency_ms,
                      result_count=0, error_class=type(e).__name__,
                      error=str(e)[:200])
        # Phase-73: update module state so caller can set day.fields
        _LAST_TV_SCAN_STATE.update({
            "status": "import_error", "result_count": 0,
            "error_class": type(e).__name__,
            "ts": _dt.now(_tz.utc).isoformat(),
        })
        log.warning("TradingView scanner not importable: %s", e)
        return []
    try:
        # Cameron defaults: premarket gap >= 5%, RVOL >= 3x, $2-$20,
        # float < 10M (matches bot's FLOAT_MAX_SHARES). The scanner returns
        # pre-sorted by premarket_change desc, top-N rows.
        rows = scan_cameron_candidates(
            top_n=top_n,
            premarket_change_min_pct=DAILY_GAIN_MIN_PCT,
            rvol_min=RVOL_MIN_PROXY,
            price_min=PRICE_MIN,
            price_max=PRICE_MAX,
            float_max_shares=FLOAT_MAX_SHARES,
        )
        latency_ms = (_t.monotonic() - t_start) * 1000
        n_rows = len(rows) if rows else 0
        _log_tv_scan(status="ok", latency_ms=latency_ms,
                      result_count=n_rows)
        # Phase-73: ok status (n=0 means "TV up but no candidates")
        _LAST_TV_SCAN_STATE.update({
            "status": "ok", "result_count": n_rows,
            "error_class": None,
            "ts": _dt.now(_tz.utc).isoformat(),
        })
        return rows
    except Exception as e:
        latency_ms = (_t.monotonic() - t_start) * 1000
        _log_tv_scan(status="error", latency_ms=latency_ms,
                      result_count=0, error_class=type(e).__name__,
                      error=str(e)[:200])
        log.warning("TradingView primary call raised: %s", e)
        # Phase-73: error status
        _LAST_TV_SCAN_STATE.update({
            "status": "error", "result_count": 0,
            "error_class": type(e).__name__,
            "ts": _dt.now(_tz.utc).isoformat(),
        })
        return []


def _log_tv_scan(*, status: str, latency_ms: float, result_count: int,
                   error_class: str | None = None,
                   error: str | None = None) -> None:
    """Phase-62: write one row to market_data_calls.jsonl describing the
    outcome of a TradingView premarket scan. Schema is stable with the
    existing yfinance/alpaca rows so a single grep can surface the full
    data-provider story for a given session."""
    try:
        import json as _j
        from datetime import datetime as _dt, timezone as _tz
        rec = {
            "ts": _dt.now(_tz.utc).isoformat(),
            "schema_version": 1,
            "source": "tradingview",
            "method": "scan_cameron_candidates",
            "status": status,                # ok | error | import_error
            "latency_ms": round(latency_ms, 2),
            "blocked_ms": 0.0,                # no guard on TV
            "error_class": error_class,
            "extra": {
                "result_count": result_count,
                **({"error": error} if error else {}),
            },
        }
        from pathlib import Path as _P
        log_path = _P(__file__).resolve().parent / "market_data_calls.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.debug("tv-scan log write failed: %s", e)


def _premarket_scan_inner(top_n: int) -> list[TickerState]:
    log.info("=" * 60)
    log.info("PREMARKET SCAN START — pulling daily bars")
    log.info("=" * 60)
    # Phase-28: TradingView scanner as PRIMARY source. One call gets top-N
    # Cameron-conformant candidates with real premarket_change /
    # premarket_volume / RVOL / float fields, no rate-limit, no API key.
    # yfinance path stays below as fallback for the rare case TV is down.
    tv_rows = _try_tradingview_primary(top_n)
    if tv_rows:
        log.info("=" * 60)
        log.info("TOP-%d WATCHLIST (source=tradingview):", top_n)
        for rank, r in enumerate(tv_rows[:top_n], start=1):
            log.info("  #%d %-6s  $%.2f  +%.1f%%  RVOL %.1fx  pmkt=%.1f%%  float=%s",
                     rank, r["ticker"], r["close"] or 0.0,
                     r["premarket_change"] or 0.0,
                     r["rvol_proxy"] or 0.0,
                     r["premarket_change"] or 0.0,
                     f"{int(r['float_shares']):,}" if r.get("float_shares") else "?")
        log.info("=" * 60)
        return [
            TickerState(
                symbol=r["ticker"],
                rank=int(rank),
                score=float((r["premarket_change"] or 0.0) * (r["rvol_proxy"] or 1.0)),
                intraday_pct=float(r["premarket_change"] or r["change_pct"] or 0.0),
                rvol_proxy=float(r["rvol_proxy"] or 0.0),
            )
            for rank, r in enumerate(tv_rows[:top_n], start=1)
        ]
    log.warning("PREMARKET SCAN: TradingView returned 0 rows — falling back to yfinance path")
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
    yfinance_missing_symbols: set[str] = set()  # Review-V2 P1.2: deferred delisted-mark
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
            # entire batch returned nothing — DEFER delisted-marking until
            # we've also checked Alpaca (Review-V2 P1.2). Track symbols.
            yfinance_missing_symbols.update(batch)
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=0, future_stack=True).rename_axis(["date","ticker"]).reset_index()
        else:
            df = df.reset_index(); df["ticker"] = batch[0]
        df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
        df = df.dropna(subset=["close","open","volume"])
        # Review-V2 P1.2: defer delisted-marking until two-source check.
        # Tickers ohne yfinance-Daten könnten nur transient down sein.
        try:
            seen = set(df["ticker"].unique()) if "ticker" in df.columns else set()
            for t in batch:
                if t not in seen:
                    yfinance_missing_symbols.add(t)
        except Exception:
            pass
        df = df.sort_values(["ticker","date"])
        df["prev_close"] = df.groupby("ticker")["close"].shift(1)
        # Audit-Iter 16 (Bug SCN-2/SCN-3): defensive against div-by-zero.
        # Wenn prev_close=0 (corrupt data) → intraday_pct=inf → False-Positive
        # passed Filter. Gleiches für avg_vol_20=0.
        df["intraday_pct"] = (df["high"] - df["prev_close"]) / df["prev_close"].replace(0, np.nan) * 100
        df["avg_vol_20"] = df.groupby("ticker")["volume"].transform(lambda s: s.rolling(20, min_periods=5).mean())
        df["rvol_proxy"] = df["volume"] / df["avg_vol_20"].replace(0, np.nan)
        # Sicherheit: nicht-finite (NaN/inf) Values rauswerfen
        df = df[
            np.isfinite(df["intraday_pct"]) & np.isfinite(df["rvol_proxy"])
        ]
        latest = df.groupby("ticker").tail(1)
        # Audit-Iter 16 (Bug SCN-7): nur Bars mit Datum heute oder gestern
        # akzeptieren — verhindert dass halted/delisted Stocks aus letzter
        # gehandelter Bar (z.B. 2 Wochen alt) im Watchlist landen.
        try:
            today_utc = pd.Timestamp.now(tz="UTC").normalize()
            min_date = today_utc - pd.Timedelta(days=4)
            latest_dt = pd.to_datetime(latest["date"], utc=True, errors="coerce")
            latest = latest[latest_dt >= min_date]
        except Exception:
            pass
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
    # Review-V2 P1.2: two_source_scan NOW WIRED.
    # Previously was dead code with TODO. Now if yfinance >20% degraded,
    # we query Alpaca for the missing symbols + use the result to:
    #   1. RECOVER candidates that yfinance missed but Alpaca knows about
    #   2. Only mark as delisted those that ALSO fail in Alpaca
    # This prevents transient yfinance outages from poisoning the
    # delisted-cache for 30 days.
    from two_source_scan import (
        should_fallback_to_alpaca, yfinance_failure_ratio,
        YFINANCE_FAIL_THRESHOLD_PCT, alpaca_universe_snapshot,
    )
    truly_delisted = set(yfinance_missing_symbols)  # default: assume all missing are delisted
    degraded = should_fallback_to_alpaca(n_batches, failed_batches)
    if degraded:
        ratio = yfinance_failure_ratio(n_batches, failed_batches)
        log.warning("=" * 60)
        log.warning("YFINANCE-DEGRADED: %.1f%% batches failed (>%.0f%% threshold)",
                    ratio, YFINANCE_FAIL_THRESHOLD_PCT)
        log.warning("→ querying Alpaca for %d missing symbols",
                    len(yfinance_missing_symbols))
        log.warning("=" * 60)
        try:
            api_key = os.environ.get("APCA_API_KEY_ID", "")
            api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
            if api_key and api_secret and yfinance_missing_symbols:
                data_client = _GuardedDC(api_key, api_secret)
                missing_list = sorted(yfinance_missing_symbols)
                # Alpaca caps batch sizes too — chunk
                alpaca_results = []
                ALP_BATCH = 500
                for j in range(0, len(missing_list), ALP_BATCH):
                    chunk = missing_list[j:j + ALP_BATCH]
                    alpaca_results.extend(alpaca_universe_snapshot(data_client, chunk))
                log.info("  Alpaca recovered %d / %d missing symbols",
                         len(alpaca_results), len(missing_list))
                # Build a candidates-DataFrame from Alpaca results that pass
                # the Cameron-Pillar 4 filter (intraday move + price range)
                alpaca_cands = []
                recovered = set()
                for sym, price, pct in alpaca_results:
                    recovered.add(sym)
                    if not (PRICE_MIN <= price <= PRICE_MAX):
                        continue
                    if pct < DAILY_GAIN_MIN_PCT:
                        continue
                    # No RVOL from Alpaca daily-bar — fallback approximation
                    # (this is BEST-EFFORT, not as good as yfinance RVOL).
                    alpaca_cands.append({
                        "ticker": sym, "close": price, "intraday_pct": pct,
                        "rvol_proxy": RVOL_MIN_PROXY,  # neutral assumption
                        "open": price, "high": price, "low": price,
                        "volume": 0.0, "prev_close": price / (1 + pct/100),
                        "avg_vol_20": 0.0,
                    })
                if alpaca_cands:
                    cands.append(pd.DataFrame(alpaca_cands))
                    log.info("  Added %d Alpaca-fallback candidates",
                             len(alpaca_cands))
                truly_delisted = yfinance_missing_symbols - recovered
                log.info("  Two-source-check: %d truly missing (yfinance+Alpaca empty), "
                         "%d transient (yfinance only)",
                         len(truly_delisted), len(recovered))
        except Exception as e:
            log.error("  Alpaca-fallback failed: %s", e)
    # Mark only TRULY-delisted symbols (verified missing in both sources OR
    # not-degraded yfinance run = trust yfinance alone)
    if truly_delisted:
        try:
            mark_batch_delisted(list(truly_delisted))
        except Exception:
            pass
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
        # Review-V2 P1.3: use configurable CATALYST_MODE.
        # "soft" (default) tolerates yfinance off-hours empty/error.
        # "strict" fails-closed for live trading.
        # Phase-26: pass gap+rvol so soft-mode can override yfinance-sparse
        # stale-news rejections when the move itself IS the catalyst.
        gap = getattr(row, "intraday_pct", None)
        rvol = getattr(row, "rvol_proxy", None)
        if CATALYST_REQUIRED and not passes_catalyst_filter(
                sym, mode=CATALYST_MODE, gap_pct=gap, rvol=rvol):
            log.info("    REJECT %s (no recent catalyst, mode=%s)", sym, CATALYST_MODE)
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
    # Phase-27 (premarket-scanner-v2 shadow): run the new Alpaca-bars-based
    # scanner side-by-side on the SAME top-N tickers, log the comparison
    # to bot.log, and write the v2-reject-reasons to
    # premarket_v2_shadow.jsonl. Decision is STILL made by the legacy
    # output below — shadow mode only collects parity evidence so we can
    # later cut over when v2 proves at least as good for N days.
    try:
        _run_premarket_v2_shadow(all_cands, top_n)
    except Exception as e:
        log.warning("premarket-v2 shadow scan failed (non-blocking): %s", e)
    # Audit-Iter 22: intraday_pct + rvol_proxy in TickerState durchreichen,
    # damit pd_size_multiplier den vollen Filter (nicht nur Score) nutzen kann.
    return [
        TickerState(
            symbol=row.ticker, rank=int(rank+1), score=float(row.score),
            intraday_pct=float(getattr(row, "intraday_pct", 0.0) or 0.0),
            rvol_proxy=float(getattr(row, "rvol_proxy", 0.0) or 0.0),
        )
        for rank, row in enumerate(all_cands.itertuples())
    ]


def _run_premarket_v2_shadow(all_cands, top_n: int) -> None:
    """Phase-27: shadow-mode invocation of premarket_scanner_v2.

    Runs the new Alpaca-bars + reject-reasons scanner on the same
    legacy-watchlist symbols, logs per-symbol pass/reject decisions,
    appends one summary row per scan to premarket_v2_shadow.jsonl.
    NEVER affects trading — pure observability."""
    import json as _json
    from pathlib import Path as _P
    from datetime import datetime as _dt, timezone as _tz

    if all_cands is None or len(all_cands) == 0:
        return
    try:
        symbols = [r.ticker for r in all_cands.itertuples()]
    except Exception:
        return
    if not symbols:
        return

    # Lazy-import alpaca data + scanner so test/non-live paths skip cleanly
    try:
        from secrets_loader import get_alpaca_keys
        from alpaca.data.historical import StockHistoricalDataClient
        from premarket_scanner_v2 import (
            scan_alpaca_premarket_with_reasons,
            scan_extended_hours_bars,
            merge_premarket_rvol_into_rows,
        )
        k, s = get_alpaca_keys()
        dc = _GuardedDC(k, s)
    except Exception as e:
        log.info("premarket-v2 shadow: deps unavailable (%s) — skipped", e)
        return

    rows = scan_alpaca_premarket_with_reasons(dc, symbols, mode="strict")
    try:
        bar_stats = scan_extended_hours_bars(dc, symbols)
    except Exception as e:
        log.warning("premarket-v2 shadow: extended-hours bars fetch failed: %s", e)
        bar_stats = {}
    rows = merge_premarket_rvol_into_rows(rows, bar_stats, mode="strict")

    n_pass = sum(1 for r in rows if r.get("passed"))
    n_total = len(rows)
    log.info("SHADOW-V2: %d/%d candidates would pass new scanner",
             n_pass, n_total)
    # Per-symbol summary
    for r in rows[:top_n]:
        passed = r.get("passed")
        reasons = r.get("reject_reasons") or []
        log.info("SHADOW-V2   %s  %s  %s",
                 r.get("ticker"),
                 "PASS" if passed else "REJECT",
                 ",".join(reasons) if reasons else "")
    # Persist for postmortem
    out_path = _P(__file__).resolve().parent / "premarket_v2_shadow.jsonl"
    try:
        record = {
            "ts": _dt.now(_tz.utc).isoformat(),
            "n_total": n_total,
            "n_pass": n_pass,
            "n_reject": n_total - n_pass,
            "rows": rows,
        }
        with out_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
    except OSError as e:
        log.warning("premarket-v2 shadow: write failed: %s", e)


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
    # Audit-Iter 20 (Bug PAT-1): vol_sma=0 (zero-volume window) → v[i]<0=False
    # → passt Filter mit nullen Volumen. Jetzt explizit: muss positiver
    # avg-Volume sein UND v[i] >= avg * factor.
    if np.isnan(vol_sma[i]) or vol_sma[i] <= 0:
        return False, {}
    if v[i] < vol_sma[i] * BREAKOUT_VOL_FACTOR:
        return False, {}

    last_local_veto = None  # Review-V2 P1.7/P1.8: track candidate-local rejects
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
            # Audit-Iter 20 (Bug PAT-3): retrace muss positiv sein. Wenn
            # fl_low > p_end (flag stieg über pole-top), retrace_pct wird
            # negativ und der Filter passt → ungewünschte Pattern-Treffer.
            retrace_amt = p_end - fl_low
            if retrace_amt < 0:
                continue
            if retrace_amt / p_h * 100 > FLAG_RETRACE_MAX_PCT: continue
            prh = h[fs:fe].max()
            if h[i] <= prh: continue
            ep = prh + SLIPPAGE_CENTS
            sp = fl_low - SLIPPAGE_CENTS
            if ep <= sp: continue
            # Trader-Loop Iter 1: Max-Risk-% filter (Cameron's "tight stops")
            # Review-V2 P1.7: candidate-local — try other pole/flag configs.
            if ep > 0:
                risk_pct = (ep - sp) / ep * 100
                if risk_pct > MAX_RISK_PCT:
                    last_local_veto = f"risk_{risk_pct:.1f}%_over_{MAX_RISK_PCT}%"
                    continue
            risk = ep - sp
            # Trader-Loop Iter 7: cap pole-extension (filter overextended setups).
            # Review-V2 P1.7: candidate-local — try other configs.
            if risk > 0 and p_h / risk > MAX_POLE_T2_R:
                last_local_veto = f"pole_h_{p_h/risk:.2f}>{MAX_POLE_T2_R}"
                continue
            # Trader-Loop Iter 25: T2 = Cameron-literal R-multiple instead
            # of pole-height. 42-day pilot: T2=2.5R gives +$70 PnL (+18%)
            # over pole-based. Sharpe 72.43→85.52. MDD unchanged.
            # 2.5R = "2.5x reward-to-risk" — Cameron's classic ratio.
            t2_R = ep + T2_R_MULTIPLE * risk
            if USE_PSYCH_LEVEL_T2:
                # nächste 0.50 above entry — psych-level upgrade if higher
                next_half = (int(ep * 2) + 1) / 2.0
                t2 = max(t2_R, next_half) if next_half > ep + 0.05 else t2_R
            else:
                t2 = t2_R
            # ─── Cameron-Vetos (heute gefixt) ─────────────────────────────
            # Phase-72: ultra-mode skips all three entry vetos so even
            # weak setups produce a trade for execution validation.
            if not DISABLE_ENTRY_VETOS:
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
    # No pole/flag config matched. Report the last local-veto if any so
    # caller can categorize the rejection (Review-V2 P1.8 telemetry).
    return False, ({"_veto": last_local_veto} if last_local_veto else {})


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
    # Audit-Iter 15 (Bug PSZ-9): negative/zero equity = broken account
    # (margin call, paper-reset). Vorher fiel der Equity-Cap raus und max_shares
    # blieb beim MAX_LOSS_PER_TRADE_USD-Limit → bot tradete trotz Konto-Problem.
    # Jetzt: explizit 0 returnen wenn equity unbrauchbar.
    if account_equity is not None and account_equity <= 0:
        return 0
    raw_risk_per_share = entry - stop
    # Minimum-Stop $0.05 (5 cents): bei engerem Stop ist Pattern-Detection
    # vermutlich Artefakt — verhindert 50000-Shares-Position
    risk_per_share = max(raw_risk_per_share, 0.05)
    max_shares = int(MAX_LOSS_PER_TRADE_USD / risk_per_share)
    # Phase-66: Equity-Cap is variant-dependent
    #   strict-algo:  1% (Cameron's original rule)
    #   relaxed-algo: 2% (doubled volume profile)
    if account_equity and account_equity > 0:
        equity_risk_cap = account_equity * (EQUITY_RISK_CAP_PCT / 100.0)
        max_shares = min(max_shares, int(equity_risk_cap / risk_per_share))
    # Quarter-Size-Rule (Iter 23: time-based fallback unlock if cents-rule
    # hasn't triggered yet but we're past the volatile open)
    quarter_active = not day.quarter_size_unlocked
    if quarter_active and ny_time is not None and ny_time >= QUARTER_SIZE_TIME_UNLOCK:
        quarter_active = False
    if quarter_active:
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


def _aggregate_open_risk(tickers: dict | None) -> float:
    """Sum of remaining risk-to-stop across all currently-open positions.
    Used by can_enter_new() for total-risk-budget gating (Review-V2 P0.5)."""
    if not tickers:
        return 0.0
    total = 0.0
    for ts in tickers.values():
        if not getattr(ts, "in_position", False):
            continue
        shares = getattr(ts, "shares", 0) or 0
        entry = getattr(ts, "entry_price", 0.0) or 0.0
        if getattr(ts, "half_filled", False):
            # post-T1: remaining shares are protected by BE-stop, so worst-case
            # they exit at break-even — zero downside on the surviving half.
            # The realized half-gain is already in day.realized_pnl.
            continue
        stop = getattr(ts, "stop_price", 0.0) or 0.0
        risk_per_share = max(entry - stop, 0.0)
        total += risk_per_share * shares
    return total


def can_enter_new(day: DayState, ny_time: dtime, *, new_trade_risk_usd: float = 0.0,
                  open_risk_usd: float = 0.0, pending_risk_usd: float = 0.0
                  ) -> tuple[bool, str]:
    """Review-V2 P0.5: now accepts open-/pending-risk to enforce a true
    daily-loss budget. Caller computes the risk-USD that THIS new entry
    would add (shares * (entry-stop)) and passes as `new_trade_risk_usd`.

    Projected worst-case = realized_loss (positive number when negative) +
    open_risk + pending_risk + new_trade_risk. If that exceeds
    DAILY_MAX_LOSS_USD, block the entry — prevents the bot from sliding
    over its daily-max in a sequence of stops.
    """
    if day.spiral_locked: return False, "spiral_locked"
    if day.realized_pnl <= -DAILY_MAX_LOSS_USD: return False, "daily_max_loss"
    # Projected-total-risk check (P0.5)
    realized_loss = max(0.0, -day.realized_pnl)
    projected = realized_loss + open_risk_usd + pending_risk_usd + new_trade_risk_usd
    if projected > DAILY_MAX_LOSS_USD:
        return False, (f"projected_risk_${projected:.2f}_exceeds_cap_${DAILY_MAX_LOSS_USD:.0f} "
                       f"(realized=${realized_loss:.2f}+open=${open_risk_usd:.2f}+"
                       f"pending=${pending_risk_usd:.2f}+new=${new_trade_risk_usd:.2f})")
    # #4 Daily-Goal-Stop
    if DAILY_GOAL_STOP_ENABLED and day.goal_reached: return False, "daily_goal_reached"
    if day.peak_pnl > 0 and day.realized_pnl < day.peak_pnl * (1 - INTRADAY_DRAWDOWN_PCT_OF_PROFITS/100):
        return False, "intraday_drawdown_50pct"
    if ny_time >= TIME_NEW_ENTRIES_END: return False, "after_1130"
    if ny_time < TIME_RTH_START: return False, "before_rth"
    if ny_time < TIME_NEW_ENTRIES_START: return False, "open_range_5min"  # Fix 12.05: kein Entry in 1. 5min
    # #5 Max trades per day (counted incl. open positions for V2-correctness)
    submitted_today = day.trades_completed_today + day.orders_submitted
    if submitted_today >= MAX_TRADES_PER_DAY:
        return False, f"max_{MAX_TRADES_PER_DAY}_trades_today_(submitted={submitted_today})"
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
    """Audit-Iter 17 (2026-05-12) — Bug-Fixes LOG-1/LOG-2/LOG-3:
      LOG-1: explicit flush + fsync — crash zwischen buffer und disk wäre
             sonst verlorene trade-events nach Cloud-Killing.
      LOG-2: threading.Lock — bot ist primär async aber on_bar Handler
             könnten parallel feuern (mehrere Symbols), JSONL würde
             corrupt mit interleaved lines.
      LOG-3: try/except — disk-full / permission-error darf nicht den
             ganzen Bot crashen mid-trade.
    """
    def __init__(self, path=None, filename: str = "trades_live.jsonl"):
        """Phase-11 (ChatGPT-18:40 P0.2): path parameterizable so Bot writes
        to trades_live.jsonl and ReplayBot writes to trades_replay.jsonl.
        Mixing replay-events into the live ledger was contaminating audit
        and post-mortem analysis. `path` (full Path) wins over `filename`."""
        import threading
        if path is not None:
            self.path = path
        else:
            self.path = DATA_DIR / filename
        self._lock = threading.Lock()

    def log(self, event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        try:
            line = json.dumps(event) + "\n"
        except (TypeError, ValueError) as e:
            log.warning("TradeLogger: cannot serialize event: %s", e)
            return
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    try:
                        import os as _os
                        _os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        pass  # fsync nicht auf allen FS verfügbar
        except (OSError, IOError) as e:
            log.warning("TradeLogger: write failed: %s", e)


class _NullTradeLogger:
    """Phase-11 (ChatGPT-18:40 P0.2): silent no-op logger for tests /
    sweeps that should not touch disk. Same .log(event) interface."""
    def log(self, event: dict):  # noqa: D401  (mirror real signature)
        pass


# ─── Alpaca-Executor ────────────────────────────────────────────────────────
class AlpacaExecutor:
    def __init__(self, api_key: str, api_secret: str, paper: bool = True, dry_run: bool = False):
        self.dry_run = dry_run
        self.client = _GuardedTC(api_key, api_secret, paper=paper)
        if dry_run:
            log.info("DRY-RUN mode: no orders submitted")
        # Phase-26: default to null loggers; Bot.__init__ injects real ones.
        from structured_logger import NullMarketDataLogger, NullOrderLifecycleLogger
        self.md_logger = NullMarketDataLogger()
        self.ol_logger = NullOrderLifecycleLogger()

    def get_equity(self) -> float:
        try:
            return float(self.client.get_account().equity)
        except Exception as e:
            log.warning("get_equity err: %s — using $25k default", e)
            return 25000.0

    def submit_buy_limit(self, symbol: str, shares: int, price: float) -> str | None:
        """LEGACY: returns order_id only, no fill-poll. Use submit_buy_with_confirm()."""
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

    def submit_buy_with_confirm(self, symbol: str, shares: int, price: float,
                                wait_fill_seconds: float = 8.0) -> dict:
        """Review-V2 P0.2 fix: Pyramid-Add with fill lifecycle.

        Submits limit buy, polls until filled / partially-filled / timeout
        / rejected. NO market-fallback (adds are optional — if they don't
        fill, the main position is unharmed).

        Returns dict (same shape as submit_sell_with_confirm):
          {"status": "filled",   "filled_qty": N, "avg_fill_price": P, ...}
          {"status": "partial",  "filled_qty": N, "remaining_qty": M, ...}
          {"status": "rejected"/"timeout", "filled_qty": 0/partial, ...}
        """
        if self.dry_run:
            log.info("[DRY] BUY %s %d @ %.2f", symbol, shares, price)
            return {"status": "filled", "filled_qty": shares,
                    "avg_fill_price": price,
                    "order_id": f"dryrun-{symbol}-{datetime.now().timestamp()}"}
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
            )
            o = self.client.submit_order(req)
            order_id = o.id
            log.info("BUY-CONFIRM %s %d @ %.2f → %s (waiting fill)",
                     symbol, shares, price, order_id)
        except Exception as e:
            log.error("submit_buy_with_confirm err %s: %s", symbol, e)
            return {"status": "failed", "reason": str(e), "filled_qty": 0}

        import time as _t
        deadline = _t.time() + wait_fill_seconds
        while _t.time() < deadline:
            _t.sleep(0.5)
            try:
                refreshed = self.client.get_order_by_id(order_id)
            except Exception:
                continue
            status_str = str(getattr(refreshed.status, "value", refreshed.status)).strip().upper().rsplit(".", 1)[-1]
            if status_str == "FILLED":
                fp = getattr(refreshed, "filled_avg_price", None)
                fq = getattr(refreshed, "filled_qty", None)
                try:
                    avg_price = float(fp) if fp else price
                except (TypeError, ValueError):
                    avg_price = price
                try:
                    fill_qty = int(float(fq)) if fq else shares
                except (TypeError, ValueError):
                    fill_qty = shares
                log.info("BUY-CONFIRM %s FILLED @ $%.4f qty=%d/%d",
                         symbol, avg_price, fill_qty, shares)
                return {"status": "filled", "filled_qty": fill_qty,
                        "avg_fill_price": avg_price, "order_id": order_id}
            if status_str in ("REJECTED", "CANCELED", "EXPIRED"):
                log.warning("BUY-CONFIRM %s status=%s", symbol, status_str)
                return {"status": "rejected", "filled_qty": 0,
                        "order_id": order_id}

        # Timeout — capture any partial, then cancel
        try:
            refreshed = self.client.get_order_by_id(order_id)
            fq = getattr(refreshed, "filled_qty", None)
            try:
                partial_qty = int(float(fq)) if fq else 0
            except (TypeError, ValueError):
                partial_qty = 0
            fp = getattr(refreshed, "filled_avg_price", None)
            try:
                partial_avg = float(fp) if fp else price
            except (TypeError, ValueError):
                partial_avg = price
        except Exception:
            partial_qty, partial_avg = 0, price
        try:
            self.client.cancel_order_by_id(order_id)
        except Exception:
            pass
        if partial_qty > 0:
            log.warning("BUY-CONFIRM %s PARTIAL %d/%d, canceled remainder",
                        symbol, partial_qty, shares)
            return {"status": "partial", "filled_qty": partial_qty,
                    "avg_fill_price": partial_avg,
                    "remaining_qty": shares - partial_qty,
                    "order_id": order_id}
        log.warning("BUY-CONFIRM %s TIMEOUT no fill — canceled", symbol)
        return {"status": "timeout", "filled_qty": 0,
                "remaining_qty": shares, "order_id": order_id}

    def submit_bracket_buy(self, symbol: str, shares: int, entry: float,
                           stop: float, take_profit: float,
                           wait_fill_seconds: float = 20.0) -> dict:
        """Cameron-Default: Entry-Limit + Stop-Loss + Take-Profit als BRACKET.

        Review-fix 2026-05-13 (Reviewer's #1 P1 concern): vorher hat diese
        Funktion order_id zurückgegeben sobald submit_order returned —
        Bot dachte position ist offen obwohl nur SUBMITTED, nicht FILLED.
        Konsequenz: in_position=True, PnL berechnet auf geplanten preisen,
        broker-state und bot-state out of sync.

        Jetzt: synchron auf Fill warten (wait_fill_seconds default 20s),
        return-dict mit ECHTEM fill_price oder failure-status. Caller
        nutzt fill_price statt geplante entry für state.

        Returns dict:
          {"status": "filled", "order_id": "...", "fill_price": 10.23,
           "shares": 100}  ← position is real, use these values
          {"status": "rejected", "order_id": "..."}  ← do NOT set in_position
          {"status": "timeout", "order_id": "..."}  ← order canceled, do NOT
          {"status": "failed", "reason": "..."}  ← submit raised, no order
        """
        if stop >= entry:
            log.error("BRACKET-BUY %s INVALID: stop %.2f >= entry %.2f — skip", symbol, stop, entry)
            return {"status": "failed", "reason": "stop>=entry"}
        if take_profit <= entry:
            log.error("BRACKET-BUY %s INVALID: tp %.2f <= entry %.2f — skip", symbol, take_profit, entry)
            return {"status": "failed", "reason": "tp<=entry"}
        # Phase-26: emit `intent` to the structured order-lifecycle log so
        # downstream postmortem sees the planned values regardless of fate.
        intent_id = self.ol_logger.emit_intent(
            symbol=symbol, side="BUY", qty=shares,
            planned_price=round(entry, 2),
            planned_stop=round(stop, 2),
            planned_target=round(take_profit, 2),
        )
        if self.dry_run:
            log.info("[DRY] BRACKET-BUY %s %d entry=%.2f stop=%.2f tp=%.2f",
                     symbol, shares, entry, stop, take_profit)
            self.ol_logger.emit_filled(
                intent_id, symbol=symbol, side="BUY", qty=shares,
                filled_qty=shares, actual_price=round(entry, 2),
                broker_order_id=f"dryrun-{symbol}",
                extra={"dry_run": True},
            )
            # In dry-run wir SIMULIEREN einen fill bei limit-price
            return {"status": "filled",
                    "order_id": f"dryrun-{symbol}-{datetime.now().timestamp()}",
                    "fill_price": entry, "shares": shares,
                    "intent_id": intent_id}
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY, limit_price=round(entry, 2),
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
                stop_loss=StopLossRequest(stop_price=round(stop, 2)),
            )
            o = self.client.submit_order(req)
            order_id = o.id
            self.ol_logger.emit_submitted(
                intent_id, symbol=symbol, side="BUY", qty=shares,
                broker_order_id=str(order_id),
            )
            log.info("BRACKET-BUY %s %d entry=%.2f STOP=%.2f TP=%.2f → %s (waiting fill)",
                     symbol, shares, entry, stop, take_profit, order_id)
        except Exception as e:
            log.error("submit_bracket_buy err %s: %s", symbol, e)
            self.ol_logger.emit_rejected(
                intent_id, symbol=symbol, side="BUY", qty=shares,
                error_class=type(e).__name__, reason=str(e)[:200],
            )
            return {"status": "failed", "reason": str(e)}
        # Poll for fill
        import time as _t
        deadline = _t.time() + wait_fill_seconds
        while _t.time() < deadline:
            _t.sleep(1)
            try:
                refreshed = self.client.get_order_by_id(order_id)
            except Exception as e:
                log.debug("fill-poll err %s: %s", symbol, e)
                continue
            status = refreshed.status
            # Status comparison robust gegen alpaca-py enum-drift (wie SB-2 fix)
            status_str = str(getattr(status, "value", status)).strip().upper().rsplit(".", 1)[-1]
            if status_str == "FILLED":
                fp = getattr(refreshed, "filled_avg_price", None)
                fq = getattr(refreshed, "filled_qty", None)
                try:
                    fill_price = float(fp) if fp else None
                except (TypeError, ValueError):
                    fill_price = None
                try:
                    fill_qty = int(float(fq)) if fq else shares
                except (TypeError, ValueError):
                    fill_qty = shares
                if fill_price is None or fill_price <= 0:
                    log.warning("BRACKET-BUY %s filled but no avg_price — using limit", symbol)
                    fill_price = entry
                log.info("BRACKET-BUY %s FILLED @ $%.4f (planned $%.2f, qty %d/%d)",
                         symbol, fill_price, entry, fill_qty, shares)
                self.ol_logger.emit_filled(
                    intent_id, symbol=symbol, side="BUY", qty=shares,
                    filled_qty=fill_qty, actual_price=fill_price,
                    broker_order_id=str(order_id),
                )
                return {"status": "filled", "order_id": order_id,
                        "fill_price": fill_price, "shares": fill_qty,
                        "intent_id": intent_id}
            if status_str in ("REJECTED", "CANCELED", "EXPIRED"):
                log.warning("BRACKET-BUY %s order %s status=%s", symbol, order_id, status_str)
                self.ol_logger.emit_rejected(
                    intent_id, symbol=symbol, side="BUY", qty=shares,
                    broker_order_id=str(order_id),
                    reason=f"broker_status={status_str}",
                )
                return {"status": "rejected", "order_id": order_id,
                        "intent_id": intent_id}
        # Timeout — cancel the unfilled order
        log.warning("BRACKET-BUY %s TIMEOUT — cancelling order %s", symbol, order_id)
        try:
            self.client.cancel_order_by_id(order_id)
        except Exception:
            pass
        self.ol_logger.emit_canceled(
            intent_id, symbol=symbol, side="BUY", qty=shares,
            broker_order_id=str(order_id), reason="wait_fill_timeout",
        )
        return {"status": "timeout", "order_id": order_id,
                "intent_id": intent_id}

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
        """LEGACY shim — submits limit sell, returns order_id only. DOES NOT
        poll fills. Use submit_sell_with_confirm() for new code. Kept for
        backwards-compat with older callers.

        Review-V2 P0.1 noted this primitive treats submit-only as fill.
        manage_position() now uses submit_sell_with_confirm() instead.
        """
        if self.dry_run:
            log.info("[DRY] SELL %s %d @ %.2f (%s)", symbol, shares, price, reason)
            return f"dryrun-{symbol}-{datetime.now().timestamp()}"
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

    def submit_sell_with_confirm(self, symbol: str, shares: int, price: float,
                                 reason: str, wait_fill_seconds: float = 8.0,
                                 market_fallback: bool = True) -> dict:
        """Review-V2 P0.1 fix: Exit-Order with full fill lifecycle.

        Cancels protection (bracket-children), submits limit sell, polls
        until filled / partially-filled / timeout / rejected. On timeout
        and market_fallback=True, cancels limit and submits market sell to
        guarantee flat.

        Returns dict:
          {"status": "filled",  "filled_qty": N, "avg_fill_price": P,
           "order_id": "..."}
          {"status": "partial", "filled_qty": N, "avg_fill_price": P,
           "remaining_qty": M, "order_id": "..."}
          {"status": "rejected", "filled_qty": 0, "order_id": "..."}
          {"status": "timeout_market_filled", ...}  ← after market fallback
          {"status": "failed", "reason": "...", "filled_qty": 0}

        Caller MUST mutate position state with filled_qty / avg_fill_price,
        not the requested values.
        """
        if self.dry_run:
            log.info("[DRY] SELL %s %d @ %.2f (%s)", symbol, shares, price, reason)
            return {"status": "filled", "filled_qty": shares,
                    "avg_fill_price": price,
                    "order_id": f"dryrun-{symbol}-{datetime.now().timestamp()}"}
        self.cancel_open_orders_for(symbol)
        # Submit the limit
        try:
            req = LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY, limit_price=round(price, 2),
            )
            o = self.client.submit_order(req)
            order_id = o.id
            log.info("SELL-CONFIRM %s %d @ %.2f (%s) → %s (waiting fill)",
                     symbol, shares, price, reason, order_id)
        except Exception as e:
            log.error("submit_sell_with_confirm submit err %s: %s", symbol, e)
            return {"status": "failed", "reason": str(e), "filled_qty": 0}

        # Poll for fill
        import time as _t
        deadline = _t.time() + wait_fill_seconds
        last_status = "PENDING"
        while _t.time() < deadline:
            _t.sleep(0.5)
            try:
                refreshed = self.client.get_order_by_id(order_id)
            except Exception as e:
                log.debug("sell-poll err %s: %s", symbol, e)
                continue
            status = refreshed.status
            status_str = str(getattr(status, "value", status)).strip().upper().rsplit(".", 1)[-1]
            last_status = status_str
            if status_str == "FILLED":
                fp = getattr(refreshed, "filled_avg_price", None)
                fq = getattr(refreshed, "filled_qty", None)
                try:
                    avg_price = float(fp) if fp else price
                except (TypeError, ValueError):
                    avg_price = price
                try:
                    fill_qty = int(float(fq)) if fq else shares
                except (TypeError, ValueError):
                    fill_qty = shares
                log.info("SELL-CONFIRM %s FILLED @ $%.4f qty=%d/%d (%s)",
                         symbol, avg_price, fill_qty, shares, reason)
                return {"status": "filled", "filled_qty": fill_qty,
                        "avg_fill_price": avg_price, "order_id": order_id}
            if status_str in ("REJECTED", "CANCELED", "EXPIRED"):
                log.warning("SELL-CONFIRM %s status=%s — NOT filled",
                            symbol, status_str)
                return {"status": "rejected", "filled_qty": 0,
                        "order_id": order_id}
        # Timeout — check for partial fill before fallback
        try:
            refreshed = self.client.get_order_by_id(order_id)
            fq = getattr(refreshed, "filled_qty", None)
            try:
                partial_qty = int(float(fq)) if fq else 0
            except (TypeError, ValueError):
                partial_qty = 0
        except Exception:
            partial_qty = 0

        # Cancel the unfilled portion
        try:
            self.client.cancel_order_by_id(order_id)
        except Exception:
            pass

        remaining = shares - partial_qty
        if partial_qty > 0:
            fp = getattr(refreshed, "filled_avg_price", None)
            try:
                partial_avg = float(fp) if fp else price
            except (TypeError, ValueError):
                partial_avg = price
            log.warning("SELL-CONFIRM %s PARTIAL fill %d/%d @ $%.4f, %d remaining",
                        symbol, partial_qty, shares, partial_avg, remaining)
        else:
            partial_avg = price

        if not market_fallback or remaining == 0:
            return {"status": "partial" if partial_qty else "timeout",
                    "filled_qty": partial_qty,
                    "avg_fill_price": partial_avg if partial_qty else 0.0,
                    "remaining_qty": remaining, "order_id": order_id}

        # Market-fallback for the remaining shares
        try:
            from alpaca.trading.requests import MarketOrderRequest
            mkt_req = MarketOrderRequest(
                symbol=symbol, qty=remaining, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            mo = self.client.submit_order(mkt_req)
            log.warning("SELL-CONFIRM %s MARKET-FALLBACK %d shares → %s",
                        symbol, remaining, mo.id)
            # Poll market-order briefly
            mkt_deadline = _t.time() + 5.0
            mkt_avg = price
            mkt_filled = 0
            while _t.time() < mkt_deadline:
                _t.sleep(0.5)
                try:
                    refreshed = self.client.get_order_by_id(mo.id)
                except Exception:
                    continue
                ms = str(getattr(refreshed.status, "value", refreshed.status)).strip().upper().rsplit(".", 1)[-1]
                if ms == "FILLED":
                    fp = getattr(refreshed, "filled_avg_price", None)
                    fq = getattr(refreshed, "filled_qty", None)
                    try:
                        mkt_avg = float(fp) if fp else price
                    except (TypeError, ValueError):
                        mkt_avg = price
                    try:
                        mkt_filled = int(float(fq)) if fq else remaining
                    except (TypeError, ValueError):
                        mkt_filled = remaining
                    break
            total_filled = partial_qty + mkt_filled
            # Weighted avg
            if total_filled > 0:
                blended = ((partial_qty * partial_avg) + (mkt_filled * mkt_avg)) / total_filled
            else:
                blended = 0.0
            return {"status": "timeout_market_filled",
                    "filled_qty": total_filled,
                    "avg_fill_price": blended,
                    "remaining_qty": shares - total_filled,
                    "order_id": order_id, "market_order_id": mo.id}
        except Exception as e:
            log.error("market-fallback submit err %s: %s", symbol, e)
            return {"status": "timeout", "filled_qty": partial_qty,
                    "avg_fill_price": partial_avg if partial_qty else 0.0,
                    "remaining_qty": remaining, "order_id": order_id}

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
            raw_qty = float(getattr(p, "qty", 0) or 0)
            qty = abs(int(raw_qty))
            if not sym or qty <= 0:
                continue
            # Review-fix 2026-05-13: SHORT-positions (qty<0) brauchen BUY zum
            # schließen, nicht SELL. Vorher würde der Fallback shorts noch
            # weiter shorten — bot würde mehr Risiko aufbauen statt flatten.
            side = OrderSide.SELL if raw_qty > 0 else OrderSide.BUY
            try:
                self.cancel_open_orders_for(sym)
                from alpaca.trading.requests import MarketOrderRequest
                self.client.submit_order(MarketOrderRequest(
                    symbol=sym, qty=qty, side=side,
                    time_in_force=TimeInForce.DAY,
                ))
                log.warning("FALLBACK market-%s %s %d submitted",
                            side.value if hasattr(side, 'value') else str(side),
                            sym, qty)
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
        # Phase-77 (ChatGPT 20260518_2103 ask #3): initialize TV-scan
        # fields to "pending" so status.json never reports None — the
        # operator's "did TV work?" question now has a real answer from
        # the very first status.json write, even before the scan runs.
        self.day.last_tradingview_scan_status = "pending"
        self.day.scanner_source = "pending"
        self.day.fallback_used = False
        self.logger = TradeLogger()
        self.api_key = api_key
        self.api_secret = api_secret
        # Audit 2026-05-13 (Option A): WS liefert 1-Min, Cameron-Pattern braucht
        # 5-Min. Aggregator schließt 1-min bars in 5-min Buckets.
        from bar_aggregator import BarAggregator
        self.aggregator = BarAggregator(bucket_minutes=BAR_AGGREGATION_MINUTES)
        # Phase-26: structured loggers wired in (NullLogger fallback so tests
        # via __new__ or partial init never crash). Live mode writes
        # market_data_calls.jsonl + order_lifecycle.jsonl side-by-side with
        # trades_live.jsonl. Each external call gets a row; each order
        # lifecycle transition gets a row keyed by intent_id.
        try:
            from structured_logger import (
                MarketDataLogger, OrderLifecycleLogger,
                MARKET_DATA_PATH, ORDER_LIFECYCLE_PATH,
            )
            self.md_logger = MarketDataLogger(MARKET_DATA_PATH)
            self.ol_logger = OrderLifecycleLogger(ORDER_LIFECYCLE_PATH)
        except Exception as e:
            log.warning("structured loggers not available: %s — using nulls", e)
            from structured_logger import NullMarketDataLogger, NullOrderLifecycleLogger
            self.md_logger = NullMarketDataLogger()
            self.ol_logger = NullOrderLifecycleLogger()
        # Wire the same loggers into the executor so AlpacaExecutor.submit_*
        # methods can emit lifecycle events alongside the bot's intent rows.
        try:
            self.executor.md_logger = self.md_logger
            self.executor.ol_logger = self.ol_logger
        except Exception:
            pass
        # Phase-26: inject md_logger into catalyst_filter so yfinance.news
        # calls show up in market_data_calls.jsonl with latency + status.
        try:
            from catalyst_filter import set_market_data_logger
            set_market_data_logger(self.md_logger)
        except Exception:
            pass
        # Phase-30: trade-event push notifications. Reuses the same alerter
        # the health-monitor uses (ntfy / Telegram / SMTP / log). Every
        # entry-fill and every exit-fill emits one push so the user sees
        # trading activity on their phone. force=True on send() bypasses
        # the 5-min debounce because each trade is a unique event.
        try:
            from alerter import make_alerter
            self.alerter = make_alerter()
        except Exception as e:
            log.warning("trade-alerter init failed: %s — pushes disabled", e)
            self.alerter = None

    def _push_trade(self, kind: str, symbol: str, shares: int,
                     price: float, pnl: float | None = None) -> None:
        """Best-effort push notification for a trade event. Never raises.

        kind: short tag like "BUY", "T1", "T2", "QUICK", "MACD", "STOP".
              Used in the alert title.
        pnl:  realized P&L in $ for this fill (None for entries).
        """
        alerter = getattr(self, "alerter", None)
        if alerter is None:
            return
        try:
            level = "info"
            day_pnl = self.day.realized_pnl
            if pnl is None:
                # Entry — no P&L yet
                title = f"BUY {symbol} {shares} @ ${price:.2f}"
                body = f"day PnL ${day_pnl:+.2f}"
            else:
                arrow = "+" if pnl >= 0 else ""
                title = f"{kind} {symbol} {shares} @ ${price:.2f} PnL {arrow}${pnl:.2f}"
                body = f"day PnL ${day_pnl:+.2f} | trade {kind}"
                if pnl < 0:
                    level = "warn"
            alerter.send(level, title, body, force=True)
        except Exception as e:
            log.debug("trade-push %s %s failed: %s", kind, symbol, e)

    async def run(self):
        log.info("=" * 60)
        log.info("CAMERON-BOT START — paper trading")
        log.info("=" * 60)
        # Phase-43: enable singleton enforcement at LIVE bot startup.
        # Not auto-installed at import time because tests would inherit
        # the global behavior and lose ability to construct multiple
        # StockDataStream instances or test for __init__ errors.
        if _enable_ws_singleton is not None:
            try:
                _enable_ws_singleton()
            except Exception as _e:
                log.warning("enable_ws_singleton failed: %s", _e)

        # 0. Connection Pre-Check
        try:
            equity = self.executor.get_equity()
            self._last_equity = equity  # Review-V2 P2.5: dashboard fix
            log.info("Alpaca-Connection OK — Account-Equity: $%.2f", equity)
        except Exception as e:
            log.error("Alpaca-Connection FAIL: %s", e, exc_info=True)
            return

        # Phase-44 (2026-05-15): "Bot started" push moved to daemon_run()
        # so it fires regardless of market state. Bot.run() only runs
        # during trading hours; daemon_run runs ALL the time. Keeping
        # the push in only one place avoids the user getting two on a
        # trading day's first scan.

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

        # 1. Premarket-Scan — Audit-Iter 30 (Bug WP-6): bei Mid-Day-Resume
        # erst load_watchlist_with_scores versuchen statt fresh re-scan.
        # Spart 60-90s scan-time bei Cloud-Restart innerhalb Trading-Window.
        # Phase-77 (ChatGPT 20260518_2103 ask #3): track WHICH path produced
        # the watchlist so status.json answers operator's "where did this
        # watchlist come from?" without grepping logs.
        candidates = None
        used_disk_resume = False
        ran_fresh_scan = False
        loaded = load_watchlist_with_scores()
        if loaded is not None and loaded[0]:
            syms, scores = loaded
            log.info("MID-DAY-RESUME: Watchlist aus Disk geladen (%d Symbols)", len(syms))
            candidates = [
                TickerState(symbol=s, rank=i+1, score=float(scores.get(s, 0.0)))
                for i, s in enumerate(syms)
            ]
            used_disk_resume = True
        if not candidates:
            candidates = await asyncio.to_thread(premarket_scan, TOP_N)
            ran_fresh_scan = True
        # Phase-77 (ChatGPT 20260518_2103 ask #3): distinguish 3 sources.
        # The old Phase-73 logic confused MID-DAY-RESUME with yfinance-
        # fallback because it only read _LAST_TV_SCAN_STATE (which stays
        # at init values when no scan ran). Now we look at WHO produced
        # the candidates list, not at the stale TV-state dict.
        tv_state = _LAST_TV_SCAN_STATE
        if used_disk_resume:
            # Watchlist came from a previous scan persisted to disk.
            # The disk file doesn't track what produced it originally,
            # so we can't say tradingview vs yfinance — but we CAN say
            # this run did not call any scanner.
            self.day.last_tradingview_scan_status = "skipped_disk_resume"
            self.day.scanner_source = "disk_cache_resume"
            self.day.fallback_used = False
        elif ran_fresh_scan:
            self.day.last_tradingview_scan_status = tv_state.get("status")
            if tv_state.get("status") == "ok" and tv_state.get("result_count", 0) > 0:
                self.day.scanner_source = "tradingview"
                self.day.fallback_used = False
            elif candidates:
                # TV failed/empty but yfinance produced rows
                self.day.scanner_source = "yfinance_fallback"
                self.day.fallback_used = True
            else:
                self.day.scanner_source = "none"
                self.day.fallback_used = False
        if not candidates:
            self.day.last_no_trade_reason = "no_watchlist"
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
            """1-Min-WS-Bar → Aggregator → ggf. 5-Min-Bar → handle_bar.

            Phase-78: emit visibility log at structured thresholds
            (bars_received == 1, 10, 100, then every 100). Operator can
            see whether bars are flowing without grepping for handle_bar
            calls or watching status.json."""
            self.day.bars_received += 1
            br = self.day.bars_received
            if br == 1 or br == 10 or br == 100 or (br > 0 and br % 100 == 0):
                log.info("BARS-FLOW: %d 1-min bars received (last=%s @ %s)",
                         br, getattr(bar, "symbol", "?"),
                         getattr(bar, "timestamp", "?"))
            try:
                self.day.last_ws_bar_ts = str(getattr(bar, "timestamp", None))
                sym = bar.symbol
                if sym not in self.tickers:
                    return  # früh raus für deleted symbols
                bar_dict = {
                    "open": float(bar.open), "high": float(bar.high),
                    "low": float(bar.low), "close": float(bar.close),
                    "volume": float(bar.volume),
                    "timestamp": bar.timestamp,
                }
                aggregated = self.aggregator.add(sym, bar_dict)
                # Phase-79.6: force-mode fires on EVERY 1-min bar (user
                # asked "jede minute gucken, selber entscheiden und
                # kaufen"). Pass the 1-min bar synthesized as a "fake"
                # 5-min bucket — handle_bar_5min's force path doesn't
                # care about bucket boundaries.
                if FORCE_ENTRY_ON_BAR and aggregated is None:
                    await self.handle_bar_5min(sym, bar_dict)
                    return
                if aggregated is None:
                    return  # 5-min-Bucket noch nicht komplett
                await self.handle_bar_5min(sym, aggregated)
            except Exception as e:
                log.error("on_bar error for %s: %s",
                          getattr(bar, "symbol", "?"), e, exc_info=True)

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
                    # Phase-60 (ChatGPT P1 follow-up): auto-generate the
                    # no-trade postmortem JSON at HARD_FLAT so operators
                    # don't have to log-archaeology every dead-day.
                    try:
                        from no_trade_postmortem import write_postmortem
                        out = write_postmortem()
                        log.info("HARD_FLAT postmortem written: %s", out.name)
                        # Push a summary alert if we had 0 trades today
                        try:
                            alerter = getattr(self, "alerter", None)
                            if alerter is not None and self.day.trades_completed_today == 0:
                                alerter.send(
                                    "info",
                                    "📊 Daily Postmortem (0 trades)",
                                    body=(f"No trades today. Postmortem written "
                                          f"to {out.name}. Tomorrow's premarket "
                                          f"starts ~06:28 ET."),
                                    force=True,
                                )
                        except Exception as _e:
                            log.debug("postmortem-push failed: %s", _e)
                    except Exception as _e:
                        log.warning("HARD_FLAT postmortem failed: %s", _e)
                    await asyncio.sleep(60)
                    return
                # SLOW Re-Scan (aligned to 5-min boundary, finishes AT round time)
                if ny >= slow_next_at:
                    await self.intraday_rescan()
                    slow_next_at = aligned_scan_start(datetime.now(NY_TZ),
                                                     RESCAN_SLOW_INTERVAL_MIN,
                                                     SCAN_HEAD_START_SLOW_SEC)
                    # Review-V2 P1.5: refresh SPY-trend every slow-rescan
                    # (5 min). Previously SPY was set once at startup and
                    # never updated — a market that opened green but turned
                    # red mid-day still got full size-multiplier.
                    try:
                        new_spy = await asyncio.to_thread(fetch_spy_today_pct)
                        new_mult = compute_spy_size_multiplier(new_spy)
                        if abs(new_spy - self.day.spy_pct_today) > 0.01:
                            log.info("SPY refresh: %.2f%% → %.2f%% (mult %.2fx → %.2fx)",
                                     self.day.spy_pct_today, new_spy,
                                     self.day.spy_size_multiplier, new_mult)
                        self.day.spy_pct_today = new_spy
                        self.day.spy_size_multiplier = new_mult
                    except Exception as e:
                        log.debug("SPY intraday refresh err: %s", e)
                # FAST Re-Scan (aligned to 1-min boundary) — Review-V2 P1.6:
                # only during the fast-phase (Power-Hour). After RESCAN_FAST_PHASE_END
                # the watchlist is stable and rescanning every minute is wasted
                # work / unnecessary Alpaca-API pressure.
                if ny >= fast_next_at and ny.time() < RESCAN_FAST_PHASE_END:
                    await self.fast_rescan_via_alpaca()
                    fast_next_at = aligned_scan_start(datetime.now(NY_TZ),
                                                     RESCAN_FAST_INTERVAL_MIN,
                                                     SCAN_HEAD_START_FAST_SEC)
                # Health-Check alle 15 Min
                if (ny - last_health).total_seconds() >= 900:
                    self._log_health()
                    last_health = ny
                    # Review-V2 P2.5: refresh _last_equity for status dashboard
                    try:
                        self._last_equity = self.executor.get_equity()
                    except Exception as e:
                        log.debug("equity refresh err: %s", e)
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

                    # Phase-76 (WS-storm fix 2026-05-19): if a PREVIOUS
                    # ws.run() thread is still alive (ws_task got cancelled
                    # but the OS thread didn't actually die — asyncio.cancel
                    # can't terminate threads), skip this iteration.
                    # Otherwise we'd add a SECOND ws.run() thread on top
                    # of the still-running first one. The Phase-43 singleton
                    # returns the same StockDataStream instance for both
                    # threads, but each thread spins its OWN event loop
                    # and BOTH call _run_forever → 2x WS connections to
                    # Alpaca → "connection limit exceeded" cascade.
                    #
                    # Today's incident (2026-05-19 17:39): 991 WS connection
                    # attempts in 60min, all hitting TimeoutError because
                    # multiple zombie threads were each trying to connect.
                    prev_thread = getattr(self, "_ws_run_thread", None)
                    if prev_thread is not None and prev_thread.is_alive():
                        log.warning("prev ws.run() thread still alive — "
                                     "skipping new spawn (avoid zombie-stack)")
                        await asyncio.sleep(5)
                        continue

                    # Run ws.run() in a thread WE OWN (not asyncio.to_thread)
                    # so we can check is_alive() on next iteration.
                    import threading as _th
                    self._ws_run_thread = _th.Thread(
                        target=ws.run, name="ws-run", daemon=True,
                    )
                    self._ws_run_thread.start()
                    # ws_task wraps the join-wait so we can asyncio.wait_for
                    # it in the resubscribe-flag-check loop below. If the
                    # join-wait task gets cancelled, the underlying ws.run
                    # thread keeps running — that's the leak we now CATCH
                    # via the is_alive() check at the top of the next iter.
                    ws_task = asyncio.create_task(
                        asyncio.to_thread(self._ws_run_thread.join)
                    )
                    while not ws_task.done():
                        await asyncio.sleep(5)
                        if self._pending_ws_resubscribe:
                            log.info("  WS re-subscribe triggered — restarting connection")
                            # Phase-68 (2026-05-18 live-fix): in current
                            # alpaca-py, stop_ws is `async def` — calling
                            # it without await leaves the stop-flag never
                            # set, so ws.run() keeps consuming forever,
                            # and the next StockDataStream + subscribe +
                            # run creates a SECOND auth on the same
                            # Alpaca account → "connection limit
                            # exceeded" cascade. Today (2026-05-18, NY
                            # 09:58 SBFM/GOVX scan) this prevented every
                            # trade. Defensive: works for both sync and
                            # async SDK shapes.
                            try:
                                _stop_result = ws.stop_ws()
                                if asyncio.iscoroutine(_stop_result):
                                    await _stop_result
                            except Exception as e:
                                log.warning("ws.stop_ws() raised: %s", e)
                            break
                    # Audit-Iter 18 (Bug WS-2): wenn stop_ws() den Thread nicht
                    # innerhalb 10s killt, force-cancel. Verhindert hängenden
                    # ws_task der den ws_loop blockiert während resubscribe.
                    if not ws_task.done():
                        try:
                            await asyncio.wait_for(ws_task, timeout=10.0)
                        except asyncio.TimeoutError:
                            log.warning("ws_task did not stop within 10s — cancelling")
                            ws_task.cancel()
                            try:
                                await asyncio.wait_for(ws_task, timeout=2.0)
                            except (asyncio.CancelledError, asyncio.TimeoutError):
                                pass
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

        # Review-fix 2026-05-13: explicit task management statt asyncio.gather.
        # Vorher: time_and_health_loop returnt nach HARD_FLAT, aber ws_loop
        # läuft weiter → bot blockt bis WS-disconnect oder externe Kill.
        # Jetzt: FIRST_COMPLETED + cancel pending → sauber raus.
        ws_task = asyncio.create_task(ws_loop(), name="ws_loop")
        time_task = asyncio.create_task(time_and_health_loop(), name="time_loop")
        try:
            done, pending = await asyncio.wait(
                {ws_task, time_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await asyncio.wait_for(t, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            # If ws_task finished first (=critical exit), trigger flatten
            if ws_task in done and not (time_task in done):
                log.warning("ws_loop ended first — emergency flatten")
                self.executor.market_close_all()
                self._log_day_summary()
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — closing all positions")
            for t in (ws_task, time_task):
                t.cancel()
            self.executor.market_close_all()
            self._log_day_summary()
        except Exception as e:
            log.error("Bot.run unhandled error: %s", e, exc_info=True)
            for t in (ws_task, time_task):
                t.cancel()
            self.executor.market_close_all()
            self._log_day_summary()

    def _pre_entry_quote_safety(self, symbol: str) -> tuple[bool, str]:
        """Review-V2 P0.4: pre-entry liquidity gate using safe_bracket.

        Fetches a fresh snapshot via Alpaca, validates:
          1. Two-sided quote exists (bid + ask both positive)
          2. Spread is reasonable (<5% of mid)
          3. Daily volume is above MIN_DAILY_VOLUME (10k default)

        If the snapshot API is unreachable, returns (True, "snapshot-unavailable")
        because in paper/dry-run modes we don't want to block entries on
        infrastructure flakiness — only on confirmed-bad data.

        For live trading with real money, this should be made fail-closed:
        no quote = no trade.
        """
        try:
            from safe_bracket import check_liquidity
        except Exception:
            return True, "safe_bracket-not-importable"
        try:
            from alpaca.data.requests import StockSnapshotRequest
            data_client = _GuardedDC(self.api_key, self.api_secret)
            req = StockSnapshotRequest(symbol_or_symbols=[symbol])
            snaps = data_client.get_stock_snapshot(req)
            snap = snaps.get(symbol) if isinstance(snaps, dict) else None
            if snap is None:
                return True, "snapshot-empty"  # not fail-closed in paper
        except Exception as e:
            log.debug("quote-safety snapshot err %s: %s", symbol, e)
            return True, f"snapshot-err"  # not fail-closed in paper
        ok, reason = check_liquidity(snap)
        return ok, reason

    async def fast_rescan_via_alpaca(self):
        """Fast-Re-Rank via Alpaca-Snapshot für aktuelle Watchlist + naher Pool."""
        from alpaca.data.requests import StockSnapshotRequest
        try:
            # Snapshot für aktuelle Watchlist
            symbols = list(self.tickers.keys())
            if not symbols:
                return
            data_client = _GuardedDC(self.api_key, self.api_secret)
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
        log.info("    rej VWAP:         %d", d.patterns_rejected_vwap)
        log.info("    rej MACD:         %d", d.patterns_rejected_macd)
        log.info("    rej FBO:          %d", d.patterns_rejected_fbo)
        log.info("    rej Risk%%:        %d", d.patterns_rejected_risk)
        log.info("    rej Pole-ext:     %d", d.patterns_rejected_pole_extension)
        log.info("    rej Risk-Budget:  %d", d.patterns_rejected_risk_budget)
        log.info("    rej Quote-Safety: %d", d.patterns_rejected_quote_safety)
        log.info("    rej Pullback#3:   %d", d.patterns_rejected_pullback_count)
        log.info("    rej Size=0:       %d", d.patterns_rejected_size_zero)
        log.info("  Orders submitted:   %d (%d failed)", d.orders_submitted, d.orders_failed)
        log.info("  Consec losses:      %d (spiral=%s)", d.consecutive_losses, d.spiral_locked)
        log.info("  WS reconnects:      %d", d.ws_reconnects)
        log.info("=" * 60)

    async def handle_bar(self, bar):
        """Backwards-Compat-Wrapper: nimmt SDK-Bar, extrahiert + delegiert
        an handle_bar_5min. Tests die noch SDK-bar direkt passen funktionieren
        weiter (sehen aber kein 5-min-Aggregation)."""
        try:
            sym = bar.symbol
            if sym not in self.tickers: return
            bar_dict = {
                "open": float(bar.open), "high": float(bar.high),
                "low": float(bar.low), "close": float(bar.close),
                "volume": float(bar.volume),
                "timestamp": bar.timestamp,
            }
            await self.handle_bar_5min(sym, bar_dict)
        except Exception as e:
            log.error("handle_bar wrapper error: %s", e, exc_info=True)

    async def handle_bar_5min(self, sym: str, bar_dict: dict):
        """Verarbeitet einen aggregierten 5-Min-Bar. Pattern-Detection +
        Position-Management. Bar-Dict hat keys: open/high/low/close/volume/
        timestamp (datetime)."""
        try:
            if sym not in self.tickers: return
            ts = self.tickers[sym]
            ts.bars.append(bar_dict)
            try:
                ts_dt = bar_dict["timestamp"]
                ny_time = ts_dt.astimezone(NY_TZ).time()
            except Exception:
                ny_time = datetime.now(NY_TZ).time()

            # Manage existing position
            if ts.in_position:
                await self.manage_position(ts, bar_dict, ny_time)
                return

            # Check if can enter new
            ok, reason = can_enter_new(self.day, ny_time)
            if not ok:
                self.day.last_no_trade_reason = reason
                return

            # Phase-79 (2026-05-19): FORCE_ENTRY_ON_BAR bypasses the
            # pattern detector entirely. On every 5-min bar where the
            # symbol has at least 2 bars AND we're not already in a
            # position, fabricate an entry signal at the current close
            # with a 1% stop / 2% target. Position-size envelope (force-
            # algo: $20 max loss / 0.5% equity) keeps the paper account
            # safe even if every trade loses.
            if FORCE_ENTRY_ON_BAR and len(ts.bars) >= 1 and not ts.in_position:
                _entry = float(bar_dict["close"])
                _stop = round(_entry * 0.99, 2)   # 1% stop
                _target1 = round(_entry * 1.01, 2)  # 1% target (T1, +1R)
                _target2 = round(_entry * 1.02, 2)  # 2% target (T2, +2R)
                signal = True
                params = {
                    "pole_candles": 1,
                    "flag_candles": 1,
                    "pole_height": _entry - _stop,
                    "entry_price": _entry,
                    "stop_price": _stop,
                    "target1": _target1,
                    "target2": _target2,
                    "_force_mode": True,
                }
                log.info("FORCE-ENTRY %s: synthetic signal close=$%.2f "
                         "stop=$%.2f T1=$%.2f T2=$%.2f (Phase-79)",
                         sym, _entry, _stop, _target1, _target2)
            else:
                # Detect bull-flag (normal path)
                signal, params = detect_bull_flag(list(ts.bars))
            if not signal:
                # Review-V2 P1.8: increment per-veto-reason counters.
                # detect_bull_flag now returns {"_veto": "<reason>"} when a
                # specific veto fired (instead of empty {}).
                veto = (params or {}).get("_veto", "")
                if veto.startswith("vwap"):
                    self.day.patterns_rejected_vwap = getattr(self.day, "patterns_rejected_vwap", 0) + 1
                    self.day.last_no_trade_reason = f"{sym}: VWAP-veto"
                elif veto.startswith("macd"):
                    self.day.patterns_rejected_macd += 1
                    self.day.last_no_trade_reason = f"{sym}: MACD-veto"
                elif veto.startswith("fbo"):
                    self.day.patterns_rejected_fbo += 1
                    self.day.last_no_trade_reason = f"{sym}: False-Breakout veto"
                elif veto.startswith("risk_"):
                    self.day.patterns_rejected_risk = getattr(self.day, "patterns_rejected_risk", 0) + 1
                    self.day.last_no_trade_reason = f"{sym}: risk>{MAX_RISK_PCT}%"
                elif veto.startswith("pole_h"):
                    self.day.patterns_rejected_pole_extension = getattr(self.day, "patterns_rejected_pole_extension", 0) + 1
                    self.day.last_no_trade_reason = f"{sym}: pole extended"
                else:
                    self.day.last_no_trade_reason = f"{sym}: no pattern detected"
                # Phase-78: emit structured DEBUG (not INFO — fires per
                # bar × symbol so high volume) but ALSO emit INFO once
                # per minute summarizing the latest reject across all
                # symbols. The summary is what the operator actually
                # needs in bot.log; the per-bar DEBUG line is for
                # postmortem grep.
                bars_n = len(ts.bars)
                log.debug("BAR-5M %s bars=%d veto=%s reason=%s",
                          sym, bars_n, veto or "no_pattern",
                          self.day.last_no_trade_reason)
                # Time-thrifty summary: only once per ~5 min per symbol
                last_summary = getattr(ts, "_last_no_pattern_summary_ts", None)
                now_ts = bar_dict.get("timestamp")
                if last_summary is None or (
                    now_ts is not None and
                    (now_ts - last_summary).total_seconds() >= 300
                ):
                    log.info("BAR-5M %s bars=%d veto=%s",
                             sym, bars_n, veto or "no_pattern")
                    ts._last_no_pattern_summary_ts = now_ts
                return
            # Guard gegen unvollständige params
            required = ("pole_candles", "flag_candles", "pole_height", "entry_price", "stop_price")
            if not all(k in params for k in required):
                self.day.last_no_trade_reason = f"{sym}: incomplete pattern params"
                log.warning("PATTERN %s: incomplete params, skip", sym)
                return
            self.day.patterns_detected += 1
        except Exception as e:
            # Review-fix 2026-05-13: vorher `getattr(bar, ...)` aber `bar` ist
            # in handle_bar_5min nicht definiert (NameError im error path).
            log.error("handle_bar_5min(%s) crashed: %s", sym, e, exc_info=True)
            return
        log.info("PATTERN %s: pole=%dx flag=%dx height=$%.2f → entry $%.2f stop $%.2f",
                 sym, params["pole_candles"], params["flag_candles"],
                 params["pole_height"], params["entry_price"], params["stop_price"])

        # Pullback-count check (3rd+ pullback skip)
        # Phase-79: force-algo bypasses this — unlimited entries per symbol
        ts.pullback_count_today += 1
        if ts.pullback_count_today >= 3 and not FORCE_ENTRY_ON_BAR:
            self.day.patterns_rejected_pullback_count += 1
            self.day.last_no_trade_reason = f"{sym}: 3rd-pullback skip"
            log.info("  REJECT %s: 3rd+ pullback today (#%d)", sym, ts.pullback_count_today)
            return

        # Position-Size
        # Review-fix 2026-05-13 (Reviewer #11): liquidity-cap + post-power-size
        # waren tot weil compute_position_size optional args ny_time/avg_volume
        # nie übergeben bekam. Jetzt liefern wir beide.
        equity = self.executor.get_equity()
        # avg_volume from rolling bar window (best-effort proxy)
        try:
            avg_vol = sum(b.get("volume", 0) for b in list(ts.bars)[-20:]) / 20
        except Exception:
            avg_vol = None
        # Phase-79.1: in force-mode the liquidity-cap (1% of avg-vol over
        # the rolling 20-bar window) drives shares to 0 when force-entry
        # fires on bars=2 because the rolling window is mostly zeros.
        # Bypass the volume signal entirely in force-mode — paper account
        # doesn't care about real liquidity, this is an execution-path
        # stress test.
        if FORCE_ENTRY_ON_BAR:
            avg_vol = None
        shares = compute_position_size(
            params["entry_price"], params["stop_price"], equity, self.day,
            avg_volume=avg_vol if avg_vol and avg_vol > 0 else None,
            ny_time=ny_time,
        )
        # Phase-79.2: force-mode floor — even when compute_position_size
        # returns 0 (e.g. quarter-size during pre-10am ET, or other
        # multipliers compound to <1), inject 5 shares so the trade
        # actually fires.
        if FORCE_ENTRY_ON_BAR and shares < 1:
            shares = 5
            log.info("  FORCE-SIZE %s: applying 5-share floor (compute returned 0)", sym)
        if shares < 1:
            self.day.patterns_rejected_size_zero += 1
            self.day.last_no_trade_reason = f"{sym}: position size zero"
            log.info("  REJECT %s: size=0 (entry $%.2f stop $%.2f risk-per-share $%.2f → max-shares 0)",
                     sym, params["entry_price"], params["stop_price"],
                     params["entry_price"] - params["stop_price"])
            return

        # Review-V2 P0.5: total-risk-budget gate. Before submitting the
        # entry, sum open + pending + new risk and ensure it's below the
        # daily-loss cap. This prevents stacking multiple concurrent
        # positions whose combined stops would exceed DAILY_MAX_LOSS_USD.
        risk_per_share = params["entry_price"] - params["stop_price"]
        new_trade_risk = max(0.0, risk_per_share) * shares
        open_risk = _aggregate_open_risk(self.tickers)
        # pending_risk: we don't currently track in-flight orders separately
        # so pass 0 — once we have an order-state-machine this should be
        # populated from submitted-but-not-filled entries.
        ok2, reason2 = can_enter_new(self.day, ny_time,
                                     new_trade_risk_usd=new_trade_risk,
                                     open_risk_usd=open_risk,
                                     pending_risk_usd=0.0)
        if not ok2:
            self.day.patterns_rejected_risk_budget = getattr(
                self.day, "patterns_rejected_risk_budget", 0) + 1
            self.day.last_no_trade_reason = reason2
            log.warning("  REJECT %s: %s", sym, reason2)
            return

        # Phase-79.5: in force-mode, bypass SPY-multiplier and pump-dump
        # filter entirely. They legitimately zero shares for the
        # ultra-runner symbols (WNW +70% gap with 405x RVOL is exactly
        # the "pump-dump" pattern those filters were designed to refuse)
        # but force-mode is specifically a stress-test of the execution
        # path, not the strategy.
        if not FORCE_ENTRY_ON_BAR:
            # #6 SPY-Size-Multiplier anwenden
            shares = int(shares * self.day.spy_size_multiplier)
            # Pump-Dump-Risiko: extremer Score ODER extreme Pct+RVOL-Kombi →
            # Position drastisch reduzieren.
            pd_mult = pd_size_multiplier(ts.score, ts.intraday_pct, ts.rvol_proxy)
            if pd_mult < 1.0:
                shares = int(shares * pd_mult)
                log.warning("  PUMP-DUMP-RISK %s (score=%.0f pct=%.1f rvol=%.1f) → size %.0fx",
                            sym, ts.score, ts.intraday_pct, ts.rvol_proxy, pd_mult)
            if shares < 1:
                self.day.patterns_rejected_size_zero += 1
                self.day.last_no_trade_reason = f"{sym}: position size zero after multiplier"
                log.info("  REJECT %s: shares=0 nach SPY-multiplier %.2fx",
                         sym, self.day.spy_size_multiplier)
                return
        else:
            # Force-mode floor — multipliers bypassed, just guarantee >=5
            if shares < 5:
                shares = 5

        # Review-V2 P0.4: pre-entry quote-safety-check (was the safe_bracket
        # module sitting dead — now WIRED into the live entry path).
        # Validates two-sided quote exists and spread is reasonable BEFORE
        # we submit, preventing the HSPT-style stale-trade-price disaster.
        # Phase-79.3: force-mode bypasses quote-safety. Paper trades don't
        # have the stale-quote disaster risk and the 5-min-bar entry
        # naturally has bar-age ~20-60s (>10s gate). Force-mode is for
        # execution-path stress testing — accept stale quotes.
        if not FORCE_ENTRY_ON_BAR:
            quote_ok, quote_reason = self._pre_entry_quote_safety(sym)
            if not quote_ok:
                self.day.patterns_rejected_quote_safety = getattr(
                    self.day, "patterns_rejected_quote_safety", 0) + 1
                self.day.last_no_trade_reason = f"{sym}: quote-safety {quote_reason}"
                log.warning("  REJECT %s: quote-safety failed (%s)", sym, quote_reason)
                return

        # Submit als BRACKET — Stop+TP broker-seitig, Position nie 'nackt'
        log.info("  SUBMITTING BRACKET-BUY %s %d shares  entry=$%.2f STOP=$%.2f TP2=$%.2f (rank=%d, spy_mult=%.1f)",
                 sym, shares, params["entry_price"], params["stop_price"],
                 params["target2"], ts.rank, self.day.spy_size_multiplier)
        # Review-fix 2026-05-13: submit_bracket_buy returns dict mit echtem
        # fill-status. Nur bei "filled" position aufmachen.
        result = self.executor.submit_bracket_buy(
            sym, shares, params["entry_price"],
            params["stop_price"], params["target2"],
        )
        if result["status"] != "filled":
            self.day.orders_failed += 1
            self.day.last_no_trade_reason = f"{sym}: entry not filled {result.get('status')}"
            log.warning("ENTRY %s NOT-FILLED (%s) — keeping in_position=False",
                        sym, result.get("status"))
            return
        self.day.orders_submitted += 1
        actual_fill_price = result["fill_price"]
        actual_shares = result["shares"]
        order_id = result["order_id"]
        # Re-compute T1 / T2 / Stop relativ zum ECHTEN fill (kann von limit
        # abweichen wenn besserer fill, oder nahe limit bei standard fill)
        actual_stop = params["stop_price"]
        # If actual fill below planned stop, recompute (HSPT-bug)
        if actual_stop >= actual_fill_price:
            risk_per_share = max(actual_fill_price * 0.05, 0.05)  # ~5% min
            actual_stop = round(actual_fill_price - risk_per_share, 2)
            log.warning("ENTRY %s: fill $%.4f below planned stop $%.2f — "
                        "recomputed stop to $%.2f", sym, actual_fill_price,
                        params["stop_price"], actual_stop)
        actual_t1 = round(actual_fill_price + (actual_fill_price - actual_stop), 2)
        actual_t2 = params["target2"]
        if actual_t2 <= actual_fill_price:
            actual_t2 = round(actual_fill_price + 2 * (actual_fill_price - actual_stop), 2)
        ts.in_position = True
        ts.entry_price = actual_fill_price       # ← echter fill, nicht plan
        ts.entry_bar_idx = self.day.bars_received
        ts.bars_since_entry = 0
        ts.stop_price = actual_stop
        ts.target1_price = actual_t1
        ts.target2_price = actual_t2
        ts.shares = actual_shares                 # ← echte qty (kann partial)
        ts.initial_shares = actual_shares
        ts.adds_count = 0
        ts.last_add_price = actual_fill_price
        ts.pole_candles = params["pole_candles"]
        ts.flag_candles = params["flag_candles"]
        ts.pole_height = params["pole_height"]
        ts.half_filled = False
        self.logger.log({
            "event": "entry", "symbol": sym, "rank": ts.rank, "score": ts.score,
            **params, "shares": actual_shares,
            "order_id": order_id, "fill_price": actual_fill_price,
            "actual_stop": actual_stop, "actual_t1": actual_t1, "actual_t2": actual_t2,
            "spy_mult": self.day.spy_size_multiplier,
        })
        # Phase-30: push entry-fill to phone
        self._push_trade("BUY", sym, actual_shares, actual_fill_price)
        # Review-V2 P0.3 fix: ACTIVELY verify broker-side protection matches
        # our recomputed stop/TP. Before this was dead code. If actual_stop
        # diverges from planned stop (HSPT-style fill below stop), the
        # bracket-children are still on the old plan — we must repair them.
        try:
            repaired = self.executor.verify_and_repair_protection(
                symbol=sym, fill_price=actual_fill_price,
                planned_stop=params["stop_price"], planned_tp=actual_t2,
                shares=actual_shares,
            )
            if repaired:
                log.warning("ENTRY %s: protection repaired (old plan superseded by actual fill)", sym)
        except Exception as e:
            log.error("ENTRY %s: verify_and_repair_protection raised — UNSAFE: %s", sym, e)
            # Note: position is open, protection state unknown. Live bot
            # should consider this a critical event but we don't auto-flat
            # — that's a follow-up safety improvement.

    async def manage_position(self, ts: TickerState, bar: dict, ny_time: dtime):
        ts.bars_since_entry += 1

        # Cameron MACD-Exit: bei bear-cross sofort raus (fade-away-Schutz)
        # Review-V2 P0.1: use confirm-variant. ONLY mutate state on confirmed fill.
        closes_now = [b["close"] for b in ts.bars]
        if len(closes_now) >= 30 and macd_bear_cross(closes_now):
            res = self.executor.submit_sell_with_confirm(
                ts.symbol, ts.shares, bar["close"] - SLIPPAGE_CENTS, "macd_bear_cross"
            )
            actual_fill = res.get("filled_qty", 0)
            actual_price = res.get("avg_fill_price", bar["close"])
            status = res.get("status")
            if actual_fill == 0:
                log.error("MACD-EXIT %s NOT-FILLED (%s) — keeping in_position=True, broker may still be long",
                          ts.symbol, status)
                return  # do NOT mutate state
            # Real PnL with actual fill price + qty
            pnl = (actual_price - ts.entry_price) * actual_fill
            if ts.half_filled:
                pnl += (ts.target1_price - ts.entry_price) * ts.t1_shares_sold
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            if actual_fill < ts.shares:
                # Partial exit — reduce shares but stay in position
                ts.shares -= actual_fill
                log.warning("MACD-EXIT %s PARTIAL %d/%d filled — %d remain",
                            ts.symbol, actual_fill, ts.shares + actual_fill, ts.shares)
                return  # don't mark fully flat, don't count as completed trade yet
            self.day.trades_completed_today += 1
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-DETECTION: 2 consecutive losses → STOP")
            else:
                self.day.consecutive_losses = 0
            self._check_daily_goal()
            self.logger.log({"event": "macd_exit", "symbol": ts.symbol,
                             "shares": actual_fill, "price": actual_price, "pnl": pnl,
                             "fill_status": status})
            log.info("  MACD-EXIT %s @ $%.4f (PnL $%.2f, status=%s)",
                     ts.symbol, actual_price, pnl, status)
            self._push_trade("MACD", ts.symbol, actual_fill, actual_price, pnl=pnl)
            ts.in_position = False
            return

        # #2 30¢-Quick-Exit: wenn 30c against entry und noch im Frühphase
        # Review-V2 P0.1: confirm-variant
        if not ts.half_filled and ts.bars_since_entry <= QUICK_EXIT_BARS_LIMIT:
            against = ts.entry_price - bar["close"]
            if against >= QUICK_EXIT_THRESHOLD_CENTS:
                res = self.executor.submit_sell_with_confirm(
                    ts.symbol, ts.shares, bar["close"] - SLIPPAGE_CENTS, "quick_exit_30c"
                )
                actual_fill = res.get("filled_qty", 0)
                actual_price = res.get("avg_fill_price", bar["close"])
                status = res.get("status")
                if actual_fill == 0:
                    log.error("QUICK-EXIT %s NOT-FILLED (%s) — keeping in_position",
                              ts.symbol, status)
                    return
                pnl = (actual_price - ts.entry_price) * actual_fill
                self.day.realized_pnl += pnl
                self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
                if actual_fill < ts.shares:
                    ts.shares -= actual_fill
                    log.warning("QUICK-EXIT %s PARTIAL %d filled, %d remain",
                                ts.symbol, actual_fill, ts.shares)
                    return
                self.day.quick_exits += 1
                self.day.trades_completed_today += 1
                if pnl <= 0:
                    self.day.consecutive_losses += 1
                    if self.day.consecutive_losses >= 2:
                        self.day.spiral_locked = True
                        log.warning("SPIRAL-DETECTION: 2 consecutive losses → STOP")
                else:
                    self.day.consecutive_losses = 0
                self._check_daily_goal()
                self.logger.log({"event": "quick_exit", "symbol": ts.symbol,
                                 "shares": actual_fill, "price": actual_price,
                                 "pnl": pnl, "fill_status": status})
                log.info("  QUICK-EXIT %s @ $%.4f PnL $%.2f (status=%s)",
                         ts.symbol, actual_price, pnl, status)
                self._push_trade("QUICK", ts.symbol, actual_fill, actual_price, pnl=pnl)
                ts.in_position = False
                return

        # #1 Position-Adding (Pyramiding) auf Winners
        # Review-V2 P0.2: confirm-variant — only mutate position state on actual fill
        if ADD_TO_WINNER_ENABLED and ts.adds_count < MAX_ADDS_PER_TRADE:
            add_trigger_price = ts.last_add_price + ADD_TRIGGER_CENTS
            if bar["high"] >= add_trigger_price and bar["close"] > ts.entry_price:
                add_shares = max(1, int(ts.initial_shares * ADD_FRACTION))
                res = self.executor.submit_buy_with_confirm(
                    ts.symbol, add_shares, add_trigger_price
                )
                actual_fill = res.get("filled_qty", 0)
                actual_price = res.get("avg_fill_price", add_trigger_price)
                status = res.get("status")
                if actual_fill == 0:
                    log.warning("ADD-TO-WINNER %s NOT-FILLED (%s) — no state change",
                                ts.symbol, status)
                    return  # main position unchanged, no protection update
                old_avg = ts.entry_price
                old_shares = ts.shares
                ts.entry_price = (old_avg * old_shares + actual_price * actual_fill) / (old_shares + actual_fill)
                ts.shares += actual_fill
                ts.adds_count += 1
                ts.last_add_price = actual_price
                self.day.adds_executed += 1
                self.logger.log({"event": "add", "symbol": ts.symbol, "shares": actual_fill,
                                 "price": actual_price, "new_avg": ts.entry_price,
                                 "total_shares": ts.shares, "adds": ts.adds_count,
                                 "fill_status": status})
                log.info("  ADD-TO-WINNER %s: +%d @ $%.4f → total %d, avg $%.4f (#%d, %s)",
                         ts.symbol, actual_fill, actual_price, ts.shares,
                         ts.entry_price, ts.adds_count, status)
                if ts.adds_count == 1:
                    ts.stop_price = old_avg
                self.executor.protect_position(
                    ts.symbol, ts.shares, stop=ts.stop_price, take_profit=ts.target2_price,
                )
                return

        # T1 — Review-V2 P0.1: confirm-variant
        if not ts.half_filled and bar["high"] >= ts.target1_price and ts.shares >= 2:
            half = ts.shares // 2
            res = self.executor.submit_sell_with_confirm(
                ts.symbol, half, ts.target1_price, "T1_50pct"
            )
            actual_fill = res.get("filled_qty", 0)
            actual_price = res.get("avg_fill_price", ts.target1_price)
            status = res.get("status")
            if actual_fill == 0:
                log.warning("T1 %s NOT-FILLED (%s) — bracket still active, retry next bar",
                            ts.symbol, status)
                return  # bracket may still hit, retry on next bar
            self.logger.log({"event": "T1", "symbol": ts.symbol,
                             "shares": actual_fill, "price": actual_price,
                             "fill_status": status})
            # T1 is half-take-profit — realized portion gain
            t1_pnl = (actual_price - ts.entry_price) * actual_fill
            self._push_trade("T1", ts.symbol, actual_fill, actual_price, pnl=t1_pnl)
            ts.half_filled = True
            ts.t1_shares_sold = actual_fill
            ts.shares -= actual_fill
            self.executor.protect_position(ts.symbol, ts.shares,
                                           stop=ts.entry_price, take_profit=ts.target2_price)
            self.day.cents_per_share_cumulative += (actual_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
                log.info("Quarter-Size-Rule UNLOCKED today")
            return
        # T2 — Review-V2 P0.1: confirm-variant
        if bar["high"] >= ts.target2_price and ts.shares > 0:
            res = self.executor.submit_sell_with_confirm(
                ts.symbol, ts.shares, ts.target2_price, "T2"
            )
            actual_fill = res.get("filled_qty", 0)
            actual_price = res.get("avg_fill_price", ts.target2_price)
            status = res.get("status")
            if actual_fill == 0:
                log.warning("T2 %s NOT-FILLED (%s)", ts.symbol, status)
                return
            if ts.half_filled:
                r1 = (ts.target1_price - ts.entry_price) * ts.t1_shares_sold
                r2 = (actual_price - ts.entry_price) * actual_fill
            else:
                r1 = 0.0
                r2 = (actual_price - ts.entry_price) * actual_fill
            pnl = r1 + r2
            self.logger.log({"event": "T2_exit", "symbol": ts.symbol,
                             "shares": actual_fill, "price": actual_price, "pnl": pnl,
                             "fill_status": status})
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            if actual_fill < ts.shares:
                ts.shares -= actual_fill
                log.warning("T2 %s PARTIAL %d filled, %d remain", ts.symbol, actual_fill, ts.shares)
                return
            self.day.consecutive_losses = 0
            self.day.trades_completed_today += 1
            self._check_daily_goal()
            self._push_trade("T2", ts.symbol, actual_fill, actual_price, pnl=pnl)
            ts.in_position = False
            return
        # Stop / BE — Review-V2 P0.1: confirm-variant with market-fallback
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            res = self.executor.submit_sell_with_confirm(
                ts.symbol, ts.shares, stop - SLIPPAGE_CENTS, "stop_or_BE",
                market_fallback=True,  # critical: must exit on stop-hit
            )
            actual_fill = res.get("filled_qty", 0)
            actual_price = res.get("avg_fill_price", stop)
            status = res.get("status")
            if actual_fill == 0:
                log.critical("STOP %s NOT-FILLED (%s) — UNPROTECTED POSITION REMAINS, will retry next bar",
                             ts.symbol, status)
                return
            pnl = (actual_price - ts.entry_price) * actual_fill
            if ts.half_filled:
                pnl += (ts.target1_price - ts.entry_price) * ts.t1_shares_sold
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            if actual_fill < ts.shares:
                ts.shares -= actual_fill
                log.critical("STOP %s PARTIAL %d filled, %d UNPROTECTED — next bar will retry",
                             ts.symbol, actual_fill, ts.shares)
                return
            self.day.trades_completed_today += 1
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-DETECTION: 2 consecutive losses → trading stopped")
            else:
                self.day.consecutive_losses = 0
            self._check_daily_goal()
            self.logger.log({"event": "stop_exit", "symbol": ts.symbol,
                            "shares": actual_fill, "price": actual_price, "pnl": pnl,
                            "reason": "stop" if not ts.half_filled else "BE",
                            "fill_status": status})
            kind = "STOP" if not ts.half_filled else "BE"
            self._push_trade(kind, ts.symbol, actual_fill, actual_price, pnl=pnl)
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
    """Validate bot end-to-end ohne Alpaca-API: streamt pilot intraday_5m durch.

    Review-V2 Phase 8 (ChatGPT 14:36-answer): ReplayBot can optionally use
    the SAME order-execution lifecycle as the live Bot. Pass `executor=`
    an AlpacaExecutor or FakeBroker; entries go through submit_bracket_buy
    and exits through submit_sell_with_confirm — same path live trades.

    When executor=None (default), uses legacy inline _manage logic. This
    preserves the 167-day backtest baseline ($581.82 / 17 trades).

    The two paths produce IDENTICAL PnL on the default "filled_at_limit"
    FakeBroker behavior — verified by tests/test_replay_executor_parity.py.
    """

    def __init__(self, executor=None, log_path=None):
        """Phase-11 (ChatGPT-18:40 P0.2): ReplayBot writes to
        trades_replay.jsonl (NOT trades_live.jsonl) so the live ledger is
        not contaminated by REPLAY_entry events. Pass log_path=None to use
        the default trades_replay.jsonl, or an explicit Path to override,
        or False to disable persistence entirely (handy for unit tests)."""
        self.tickers: dict[str, TickerState] = {}
        self.day = DayState()
        if log_path is False:
            self.logger = _NullTradeLogger()
        elif log_path is None:
            self.logger = TradeLogger(filename="trades_replay.jsonl")
        else:
            self.logger = TradeLogger(path=log_path)
        self.equity = 25_000.0  # paper-default
        self.executor = executor  # Phase 8: optional shared execution layer

    def submit_buy(self, sym, shares, price): log.info("[REPLAY] BUY %s %d @ %.2f", sym, shares, price)
    def submit_sell(self, sym, shares, price, reason): log.info("[REPLAY] SELL %s %d @ %.2f (%s)", sym, shares, price, reason)

    def run(self, target_date: str):
        bars_path, cands_path = find_pilot_data_paths()
        if bars_path is None:
            log.error("Need pilot data — looked at: backtest_data/ and "
                      "04_backtest/data_pilot/. Run 04_backtest/bootstrap.py "
                      "or ensure backtest_data/intraday_5m.parquet exists.")
            return

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
            if not ok:
                # Phase-71 (ChatGPT 20260517_2233 P4): replay path was missing
                # the last_no_trade_reason set that the live on_bar path
                # already has at line 2607. Now both surface the can_enter
                # veto reason to the operator status.
                self.day.last_no_trade_reason = reason
                continue
            signal, params = detect_bull_flag(list(ts.bars))
            if not signal:
                self.day.last_no_trade_reason = f"{sym}: no pattern detected"
                continue
            ts.pullback_count_today += 1
            if ts.pullback_count_today >= 3:
                self.day.last_no_trade_reason = f"{sym}: 3rd-pullback skip"
                continue
            shares = compute_position_size(
                params["entry_price"], params["stop_price"], self.equity, self.day,
                ny_time=ny_t)  # Iter 23: needed for time-based quarter-unlock
            if shares < 1:
                self.day.last_no_trade_reason = f"{sym}: position size zero"
                continue
            # Phase-8: route entry through executor if injected, else legacy
            if self.executor is not None:
                res = self.executor.submit_bracket_buy(
                    sym, shares, params["entry_price"],
                    params["stop_price"], params["target2"],
                )
                if res.get("status") != "filled":
                    continue  # not filled — no position-state mutation
                entry_price = res.get("fill_price", params["entry_price"])
                filled_shares = res.get("shares", shares)
            else:
                self.submit_buy(sym, shares, params["entry_price"])
                entry_price = params["entry_price"]
                filled_shares = shares
            ts.in_position = True
            ts.entry_price = entry_price; ts.stop_price = params["stop_price"]
            ts.target1_price = params["target1"]; ts.target2_price = params["target2"]
            ts.shares = filled_shares; ts.half_filled = False
            ts.bars_since_entry = 0  # Iter 9: reset for QE-tracking
            ts.t1_shares_sold = 0
            self.logger.log({"event": "REPLAY_entry", "symbol": sym, "rank": ts.rank, **params, "shares": filled_shares})

        # End-of-day report
        log.info("=" * 60)
        log.info("REPLAY DONE — %s", target_date)
        log.info("  Daily realized PnL: $%.2f", self.day.realized_pnl)
        log.info("  Peak PnL:           $%.2f", self.day.peak_pnl)
        log.info("  Consecutive losses: %d (spiral_locked=%s)",
                 self.day.consecutive_losses, self.day.spiral_locked)
        log.info("=" * 60)

    def _executor_sell(self, ts, qty, price, reason):
        """Phase-8 helper: route exit through self.executor if injected,
        else through legacy submit_sell. Returns (filled_qty, fill_price)
        with confirm-style semantics (filled_qty may differ from qty for
        partial-fill scenarios). Uses getattr fallback so legacy tests
        that construct ReplayBot via __new__ (bypassing __init__) still work.

        Phase-17 (ChatGPT-12:52 P2.x golden scenario "exit rejected then
        fallback"): if the broker returns status=rejected with
        retryable=True, attempt ONE retry on the same symbol. The
        FakeBroker's `reject_then_market` behavior consumes the per-symbol
        override on first rejection so the retry sees the default fill
        behavior — mirroring a transient broker reject followed by a
        clean retry."""
        executor = getattr(self, "executor", None)
        if executor is not None:
            res = executor.submit_sell_with_confirm(ts.symbol, qty, price, reason)
            filled = res.get("filled_qty", 0)
            if (filled == 0
                    and res.get("status") == "rejected"
                    and res.get("retryable")):
                log.warning("REPLAY %s exit rejected (%s) — retrying once",
                            ts.symbol, reason)
                res = executor.submit_sell_with_confirm(
                    ts.symbol, qty, price, f"{reason}_retry")
                filled = res.get("filled_qty", 0)
            return filled, res.get("avg_fill_price", price)
        self.submit_sell(ts.symbol, qty, price, reason)
        return qty, price  # legacy: assume full fill at limit

    def _verify_stop_protection(self, ts):
        """Phase-17 (ChatGPT-12:52 P2.x golden scenario "missing stop
        repaired"): on each bar where we believe we're in a position,
        ask the broker-truth (FakeBroker / AlpacaExecutor) whether a
        STOP-type protection order is still active. If not, re-submit
        via protect_position(). No-op for legacy path (executor=None)."""
        executor = getattr(self, "executor", None)
        if executor is None:
            return False
        if not ts.in_position or ts.shares <= 0:
            return False
        # Probe broker-truth — only act if the executor exposes the API
        has_stop = getattr(executor, "has_stop_protection", None)
        if has_stop is None:
            return False
        if has_stop(ts.symbol):
            return False
        # Stop is missing — repair via protect_position. Use the
        # half_filled BE-stop if we've already taken T1, else the
        # original stop_price.
        stop_now = ts.entry_price if ts.half_filled else ts.stop_price
        protect = getattr(executor, "protect_position", None)
        if protect is None:
            return False
        log.critical("REPLAY %s STOP MISSING — repairing via protect_position "
                     "(shares=%d, stop=%.2f, target=%.2f)",
                     ts.symbol, ts.shares, stop_now, ts.target2_price)
        protect(ts.symbol, ts.shares, stop_now, ts.target2_price)
        return True

    def _book_t1_pnl_once(self, ts):
        """Phase-10 (ChatGPT-18:20): T1-leg PnL must be booked exactly ONCE
        across all subsequent T2-partial-fills and/or BE-stop fills.
        Previously each T2-partial added the full T1-gain again, double-
        counting on `partial T2 → final T2` paths. Returns the T1-leg PnL
        on first call when half_filled, 0.0 thereafter."""
        if not ts.half_filled or getattr(ts, "_replay_t1_pnl_booked", False):
            return 0.0
        setattr(ts, "_replay_t1_pnl_booked", True)
        return (ts.target1_price - ts.entry_price) * ts.t1_shares_sold

    def _manage(self, ts, bar, ny_t):
        """Audit-Iter 19 (2026-05-12) — Replay-Live-Parität:
          REP-1: T2-Exit zählte T1-Gewinn nicht (mirrored live bot fix MP-1/PYR-1)
          REP-2: Stop-Exit zählte T1-Gewinn nicht
          REP-5: trades_completed_today wurde nicht incremented → MAX_TRADES_PER_DAY
                 greift nicht in Replay → unrealistic
        Phase-8 (2026-05-14): exits now route through self._executor_sell so
        when a FakeBroker is injected, ReplayBot drives the SAME order-
        lifecycle code-path live trades use. Default-behavior parity verified.
        Phase-17: stop-protection probe on every bar — re-submits missing
        stop via protect_position() so a broker-dropped STOP leg doesn't
        leave the position unprotected.
        """
        ts.bars_since_entry += 1
        # Phase-17: detect + repair missing stop protection
        self._verify_stop_protection(ts)
        if (not ts.half_filled
                and ts.bars_since_entry <= QUICK_EXIT_BARS_LIMIT
                and (ts.entry_price - bar["low"]) >= QUICK_EXIT_THRESHOLD_CENTS):
            qe_px = ts.entry_price - QUICK_EXIT_THRESHOLD_CENTS
            requested = ts.shares
            filled, fill_price = self._executor_sell(ts, requested, qe_px, "QE")
            if filled == 0:
                return  # broker rejected — keep position
            # Phase-9 (ChatGPT-17:49): book PnL only on actually-filled qty;
            # decrement shares precisely; only flat when shares==0
            pnl = (fill_price - ts.entry_price) * filled
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            ts.shares -= filled
            if ts.shares > 0:
                log.warning("QE %s PARTIAL %d/%d — %d remain, in_position=True",
                            ts.symbol, filled, requested, ts.shares)
                return  # partial: stay in position, no trade-count yet
            self.day.trades_completed_today += 1
            if pnl <= 0:
                self.day.consecutive_losses += 1
                if self.day.consecutive_losses >= 2:
                    self.day.spiral_locked = True
                    log.warning("SPIRAL-LOCK after 2 losses (QE)")
            else:
                self.day.consecutive_losses = 0
            ts.in_position = False
            return
        if not ts.half_filled and bar["high"] >= ts.target1_price:
            half = max(1, ts.shares // 2)
            filled, fill_price = self._executor_sell(ts, half, ts.target1_price, "T1")
            if filled == 0:
                return
            ts.half_filled = True
            ts.t1_shares_sold = filled
            ts.shares -= filled
            self.day.cents_per_share_cumulative += (fill_price - ts.entry_price)
            if self.day.cents_per_share_cumulative >= QUARTER_SIZE_UNLOCK_CENTS:
                self.day.quarter_size_unlocked = True
            return
        if ts.half_filled and bar["high"] >= ts.target2_price:
            requested = ts.shares
            filled, fill_price = self._executor_sell(ts, requested, ts.target2_price, "T2")
            if filled == 0:
                return
            # Phase-9: T1-gain on the sold-half always counted; T2-leg counts
            # only actually-filled shares. Trade-counted only when fully flat.
            # Phase-10 (ChatGPT-18:20): r1 booked ONCE via _book_t1_pnl_once
            # so multiple T2-partials don't double-count the T1-leg.
            r1 = self._book_t1_pnl_once(ts)
            r2 = (fill_price - ts.entry_price) * filled
            pnl = r1 + r2
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            ts.shares -= filled
            if ts.shares > 0:
                log.warning("T2 %s PARTIAL %d/%d — %d remain, in_position=True",
                            ts.symbol, filled, requested, ts.shares)
                return
            self.day.consecutive_losses = 0
            self.day.trades_completed_today += 1
            ts.in_position = False
            return
        stop = ts.stop_price if not ts.half_filled else ts.entry_price
        if bar["low"] <= stop:
            requested = ts.shares
            filled, fill_price = self._executor_sell(ts, requested, stop, "stop")
            if filled == 0:
                return
            # Phase-9: PnL on filled only; T1-gain counts (was previously
            # half_filled); partial stop is HIGH-SEVERITY simulated risk —
            # log critical to surface in tests
            pnl = (fill_price - ts.entry_price) * filled
            if ts.half_filled:
                # Phase-10 (ChatGPT-18:20): T1-leg booked once across stop /
                # T2-partial paths. _book_t1_pnl_once returns 0 if already
                # booked by an earlier T2-partial.
                pnl += self._book_t1_pnl_once(ts)
            self.day.realized_pnl += pnl
            self.day.peak_pnl = max(self.day.peak_pnl, self.day.realized_pnl)
            ts.shares -= filled
            if ts.shares > 0:
                log.critical("STOP %s PARTIAL %d/%d — %d UNPROTECTED REMAIN",
                             ts.symbol, filled, requested, ts.shares)
                return  # next bar's _manage will retry stop (legacy) or
                        # executor's market-fallback path
            self.day.trades_completed_today += 1
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
        client = _GuardedTC(api_key, api_secret, paper=True)
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
    client = _GuardedTC(api_key, api_secret, paper=True)
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


# Pilot-Daten-Pfad mit Fallback. Review-fix 2026-05-13: gelieferte
# REVIEW_PACKAGE hat `backtest_data/`, repo hat `04_backtest/data_pilot/`.
def find_pilot_data_paths() -> tuple[Path | None, Path | None]:
    """Returns (bars_path, candidates_path) oder (None, None)."""
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "backtest_data",
        root / "04_backtest" / "data_pilot",
    ]
    for d in candidates:
        bars = d / "intraday_5m.parquet"
        cands = d / "candidates.parquet"
        if bars.exists() and cands.exists():
            return bars, cands
    return None, None


# ─── Daemon Mode (sleep until premarket, run one day, repeat) ──────────────
# DST-aware via zoneinfo. Vorher: fixed UTC-4 brach im Winter (EST = UTC-5).
try:
    from zoneinfo import ZoneInfo
    NY_TZ = ZoneInfo("America/New_York")
except ImportError:
    # Python <3.9 fallback (shouldn't happen in our setup)
    NY_TZ = timezone(timedelta(hours=-4))
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
    _label_map = {
        "relaxed": "2x volume, Cameron-strict entries — relaxed-algo",
        "loose": "2x volume + Phase-33 loose entries + catalyst OFF — loose-algo",
        "ultra": "2x volume + ULTRA-loose entries + VWAP/MACD/FBO disabled — ultra-algo",
        "strict": "Cameron-strict — strict-algo",
    }
    log.info("STRATEGY_VARIANT = %s (%s)", STRATEGY_VARIANT,
              _label_map.get(STRATEGY_VARIANT, "unknown"))
    log.info("  MAX_LOSS_PER_TRADE_USD = $%.0f", MAX_LOSS_PER_TRADE_USD)
    log.info("  DAILY_MAX_LOSS_USD     = $%.0f", DAILY_MAX_LOSS_USD)
    log.info("  EQUITY_RISK_CAP_PCT    = %.1f%%", EQUITY_RISK_CAP_PCT)
    log.info("  DAILY_GAIN_MIN_PCT     = %.1f%%", DAILY_GAIN_MIN_PCT)
    log.info("  RVOL_MIN_PROXY         = %.1fx", RVOL_MIN_PROXY)
    log.info("  POLE_MIN_MOVE_PCT      = %.1f%%", POLE_MIN_MOVE_PCT)
    log.info("  POLE_TOPPING_TAIL_MAX  = %.2f", POLE_TOPPING_TAIL_MAX)
    log.info("  FLAG_RETRACE_MAX_PCT   = %.1f%%", FLAG_RETRACE_MAX_PCT)
    log.info("  BREAKOUT_VOL_FACTOR    = %.2f", BREAKOUT_VOL_FACTOR)
    log.info("  CATALYST_MODE          = %s", CATALYST_MODE)
    if DISABLE_ENTRY_VETOS:
        log.warning("  DISABLE_ENTRY_VETOS = True (ultra-algo skips VWAP/MACD/FBO)")
    if SKIP_HARD_FLAT_TODAY:
        log.warning("  SKIP_HARD_FLAT_TODAY=1 — afternoon trading enabled")
        log.warning("    TIME_NEW_ENTRIES_END = %s NY  (was 11:30)",
                     TIME_NEW_ENTRIES_END.strftime("%H:%M"))
        log.warning("    TIME_HARD_FLAT       = %s NY  (was 12:00)",
                     TIME_HARD_FLAT.strftime("%H:%M"))
    else:
        log.info("  TIME_HARD_FLAT         = %s NY (Cameron-strict)",
                  TIME_HARD_FLAT.strftime("%H:%M"))
    log.info("=" * 60)

    # Pre-Flight: verify auth, WS-init, yfinance — verhindert 2026-05-11-Geistermodus
    if not run_preflight(api_key, api_secret):
        log.error("Pre-Flight FAIL — daemon aborts (fix config + restart)")
        return

    # Position-Recovery: bei Crash/Restart mit offenen Positions → flatten.
    # Audit-Iter 6: return-value checken, bei FAILED nicht weiterstarten.
    try:
        from alpaca.trading.client import TradingClient
        _rc = recover_or_flatten(_GuardedTC(api_key, api_secret, paper=True))
        if _rc == -1:
            log.error("=" * 60)
            log.error("POSITION-RECOVERY FAILED — bot wartet 5min und versucht erneut")
            log.error("=" * 60)
            await asyncio.sleep(300)
            # Zweiter Versuch
            _rc = recover_or_flatten(_GuardedTC(api_key, api_secret, paper=True))
            if _rc == -1:
                log.error("RECOVERY-RETRY auch failed — daemon aborts (manuell prüfen!)")
                return
    except Exception as e:
        log.error("position-recovery raised: %s — daemon aborts", e, exc_info=True)
        return

    # Phase-44 (user-fix 2026-05-15): "Bot started" push at DAEMON boot,
    # not inside Bot.run(). When the daemon spawns after market hours,
    # Bot.run() is never called until next premarket — so the Phase-37
    # startup push (which was inside Bot.run) never fired. Move it here
    # so the user gets a notification on EVERY daemon boot regardless
    # of market state.
    try:
        from alerter import make_alerter
        from alpaca.trading.client import TradingClient
        _alerter = make_alerter()
        if _alerter is not None:
            try:
                _eq = float(_GuardedTC(api_key, api_secret, paper=True).get_account().equity)
            except Exception:
                _eq = 0.0
            _alerter.send(
                "info",
                "Bot started",
                body=f"Cameron-Bot daemon live — equity ${_eq:,.2f}",
                force=True,
            )
    except Exception as _e:
        log.debug("daemon startup push failed: %s", _e)
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
        # Review-V2 P2.6: convert NY → Berlin via ZoneInfo (DST-aware), not
        # fixed timedelta(hours=6) which is wrong half the year.
        try:
            from zoneinfo import ZoneInfo
            berlin_str = next_start.astimezone(ZoneInfo("Europe/Berlin")).strftime("%H:%M")
        except Exception:
            berlin_str = "?"  # fallback if zoneinfo unavailable
        log.info("Next premarket-scan: %s ET (in %.1f h = %s Berlin)",
                 next_start.strftime("%Y-%m-%d %H:%M"),
                 wait_hours, berlin_str)
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
                log.info("ALIVE — sleeping. Next scan in %.1f h at %s Berlin",
                         remaining_h, berlin_str)
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
    p.add_argument("--force-lock", action="store_true",
                    help="Phase-62: steal a stale process lock from a dead "
                          "prior instance (use only when sure no other bot "
                          "is running)")
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

    # Phase-62 (ChatGPT 1817/1952/2012/2048 P0/P1 follow-up): refuse to
    # start a second instance with the same Alpaca credentials so the
    # account-wide WS connection-limit can't be tripped by two bots
    # running side-by-side. Lock is process-PID-based, OS-portable,
    # auto-stolen on prior-death. Released via atexit.
    from process_lock import enforce_single_instance_or_exit, release_lock
    import atexit
    enforce_single_instance_or_exit(force=args.force_lock)
    atexit.register(release_lock)

    if args.daemon:
        asyncio.run(daemon_run(api_key, api_secret, dry_run=args.dry_run))
        return

    bot = Bot(api_key, api_secret, dry_run=args.dry_run)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
