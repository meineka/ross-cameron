# Architecture

## System Overview

```
┌────────────────────────────────────────────────────────────────┐
│  Cloud (GitHub Actions, Mo-Fr 09:47 UTC = 11:47 CEST)         │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │  daemon_run()                                             │ │
│  │    ├ run_preflight()  ← Auth, WS, yfinance, equity check │ │
│  │    ├ recover_or_flatten() ← positions from last crash    │ │
│  │    ├ MID_DAY_RESUME or new premarket_scan()              │ │
│  │    └ Bot.run()                                            │ │
│  │       ├ premarket_scan()  ← 5-pillars filter             │ │
│  │       ├ fetch_spy_today_pct() → spy_size_multiplier      │ │
│  │       ├ async gather:                                     │ │
│  │       │   ├ ws_loop() — StockDataStream (1-min bars)     │ │
│  │       │   │   └ on_bar → aggregator (1m→5m) → handle_bar │ │
│  │       │   └ time_and_health_loop() — 12:00 HARD_FLAT     │ │
│  │       └ market_close_all() — atomic with retry           │ │
│  └──────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
       │                          │
       ▼                          ▼
   Alpaca API              yfinance / NASDAQ-Trader CSV
   (paper)                 (premarket data)
```

## Module Map

### Core (`06_live_bot/`)

| File | Purpose | Lines | Notes |
|------|---------|------:|-------|
| `bot.py` | Main daemon, orchestrator | ~1700 | Pattern detection, position management, time-cuts |
| `bar_aggregator.py` | 1-min → 5-min bucket aggregator | ~100 | Added Iter 30 (Option A) to bridge WS/Cameron-spec timeframe mismatch |
| `indicators.py` | MACD, RSI, FBO veto | ~140 | Pure math, no I/O |
| `vwap_filter.py` | Session-VWAP computation | ~80 | Defensive against missing keys, neg vol |
| `catalyst_filter.py` | yfinance news lookup | ~80 | TTL cache, strict mode opt-in |
| `float_filter.py` | yfinance float lookup | ~50 | 12h TTL, 5min for None |
| `pump_dump_filter.py` | Score-based size reducer | ~40 | Wired (Iter 22 fixed dead-code) |
| `safe_bracket.py` | Pre-fill liquidity + post-fill repair | ~180 | Bracket-order safety net |
| `pre_flight.py` | Auth + WS + yfinance smoke-test | ~100 | Fails early before trading |
| `position_recovery.py` | Crash-restart position handling | ~110 | Retry+poll, returns -1 on FAILED |
| `watchdog.py` | Process monitor + restart | ~150 | Crashloop-protection, cmdline-specific |
| `deploy_safe.py` | Graceful redeploy (no-positions check) | ~160 | SIGTERM-first, cross-platform |

### Persistence (`06_live_bot/`)

| File | What it persists | Atomic? |
|------|------------------|---------|
| `delisted_cache.py` | Known-dead tickers (30d TTL) | yes (Iter 23) |
| `watchlist_persist.py` | Today's top-10 + scores | yes (Iter 30) |
| `status_dashboard.py` | Live bot state (status.json) | yes (Iter 26) |
| `day_summary_persist.py` | End-of-day summary JSON | yes (Iter 28) |
| `slippage_log.py` | Per-fill slippage measurements | append + fsync (Iter 17) |
| `secrets_loader.py` | API-key loader (.env support) | read-only |
| `universe_cache` (in bot.py) | NASDAQ-Trader CSV cache (4h TTL) | yes (Iter 25) |

### Supporting

| File | Purpose |
|------|---------|
| `reconnect_backoff.py` | Exponential WS-reconnect backoff + circuit breaker |
| `audit.py` | Health-check script (process, log, positions) |
| `micro_test_trade.py` | 1-share manual smoke-test |
| `backtest_day.py` | Replay one historical day |
| `compare_retrace_threshold.py` | Threshold A/B comparison tool |
| `config_sweep.py` | Multi-config grid search |
| `backtest_report.py` | Aggregate stats over all pilot days |
| `replay_today*.py` | Live-data diagnostic scripts (4 versions) |
| `debug_cnck.py` | Symbol-specific filter inspection |

## Data Flow

### Premarket (12:27 CEST / 06:27 ET)
```
fetch_us_universe()  ←  cached 4h; NASDAQ-Trader CSV + atomic-write fallback
        │
        ▼
filter_known_delisted()  ←  delisted_cache (30d TTL, persisted)
        │
        ▼
yfinance.download(45d, daily-bars)  ←  retry, batched
        │
        ▼
5-Pillars filter (price/volume/intraday%/float/catalyst)
        │
        ▼
Top-N watchlist (TOP_N=10)  ←  save to watchlist_today.json (atomic)
```

### Trading (09:35 - 11:30 ET)
```
WS bar (1-min from Alpaca)
        │
        ▼
bar_aggregator.add(symbol, bar)  ←  5-min bucket builder
        │
        ▼ (only when 5-min bucket emits)
handle_bar_5min(symbol, bar5m)
        │
        ├─ if in_position: manage_position()
        │   ├─ macd_bear_cross → MACD exit
        │   ├─ quick_exit (-30¢ in 5 bars) → emergency exit
        │   ├─ pyramid add (+10¢ above last_add)
        │   ├─ T1 (50%) at target1
        │   ├─ T2 (full) at target2
        │   └─ stop / BE-stop
        │
        └─ if not in_position:
            ├─ can_enter_new() check (time, spiral, daily-goal)
            ├─ detect_bull_flag(bars5) → maybe signal
            ├─ compute_position_size() + spy_mult + pd_mult
            └─ submit_bracket_buy() ← BRACKET (entry + stop + TP atomic)
                └─ verify_and_repair_protection() if needed
```

### End of Day (12:00 ET / 16:00 UTC)
```
HARD_FLAT triggered in time_and_health_loop()
        │
        ▼
executor.market_close_all()
        ├─ pre-list positions
        ├─ close_all_positions(cancel_orders=True) — retry 3x
        ├─ poll get_all_positions() until empty
        ├─ if not flat after retries → per-position MarketOrder fallback
        └─ final verify, returns bool
        │
        ▼
_log_day_summary() + write_day_summary() (atomic)
```

## Threading / Async Model

- **Single asyncio event loop** in `Bot.run()`
- **`ws_loop`** runs `ws.run()` in a thread (asyncio.to_thread)
  - `ws.run()` is blocking Alpaca SDK internal; thread isolates it
  - `ws_task.cancel()` with 10s+2s timeouts in case stop_ws hangs
- **`on_bar`** is async, invoked by SDK via `run_coroutine_threadsafe`
- **No explicit thread locks** in trade-state because asyncio is cooperative
  (only one task active at a time between awaits)
- **`threading.Lock`** on logging targets (TradeLogger, slippage_log) because
  multiple async callers can serialize-fight on the same FD

## State

### `DayState` (per-day)
- realized_pnl, peak_pnl, consecutive_losses
- spiral_locked, goal_reached
- trades_completed_today, adds_executed, quick_exits
- spy_pct_today, spy_size_multiplier
- bars_received, ws_reconnects
- patterns_detected + per-veto rejection counters

### `TickerState` (per-symbol)
- rank, score, intraday_pct, rvol_proxy (filled at scan time)
- in_position, entry_price, stop_price, target1_price, target2_price
- shares, initial_shares, t1_shares_sold (post-Iter-12 for pyramiding PnL)
- half_filled, adds_count, last_add_price, bars_since_entry
- bars: deque(maxlen=80) — rolling 5-min window

## Deployment

### Local (Windows)
- `start_all.bat` → bot + watchdog detached
- Watchdog every 5 min checks bot.py-daemon process
- Crashloop-protection: 5 restarts/h → watchdog exits with critical log

### Cloud (GitHub Actions)
- `.github/workflows/daily-trading.yml`
- Cron `47 9 * * 1-5` UTC (off top-of-hour to avoid GH schedule contention)
- 6h timeout, bot itself has 340min wallclock
- Artifacts: daemon.log + results/ + watchlist + slippage
- Public repo = unlimited Actions minutes
- Secrets: APCA_API_KEY_ID + APCA_API_SECRET_KEY via gh secret

## Testing strategy

| Type | Count | Purpose |
|------|------:|---------|
| Unit (helpers, math) | ~250 | Pure-fn correctness |
| Behavior (mocked deps) | ~150 | Module behavior with mocked SDKs |
| Source-grep regression | ~30 | Ensures key code patterns persist |
| Integration (ReplayBot) | ~20 | End-to-end with pilot 5-min bars |
| Replay-regression | 3 | $13.14 baseline + scan + delisted-skip |

Subprocess tests skipped in `pytest -q` runs:
- `test_replay_regression.py` — subprocess call to `bot.py --replay`
- `test_pilot_baseline.py` — needs live yfinance
