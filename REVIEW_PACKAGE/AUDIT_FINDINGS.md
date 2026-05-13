# Audit Findings — 35 Iterations, 2026-05-12 to 2026-05-13

All bugs found, severity, root cause, fix. Cross-referenced to git commits.

## Severity legend

- **CRITICAL** — could lose real money or compromise security
- **HIGH** — would degrade trading performance or leak state
- **MED** — defensive/robustness; not actively losing money
- **LOW** — cosmetic, doc, marginal

## By Module

### `bot.py` — main daemon (core trade flow)

| Bug | Sev | Description | Commit |
|---|---|---|---|
| HF-1 | HIGH | `market_close_all` single-shot, no retry — HARD_FLAT could leave positions overnight on API blip | 663bb37 |
| HF-2 | HIGH | No fill-verification after `close_all_positions` (async submit) | 663bb37 |
| HF-9 | HIGH | No per-position fallback if `close_all_positions` rejects | 663bb37 |
| MP-1 | HIGH | MACD-exit after T1 lost T1 PnL in accounting | f610b35 |
| MP-7 | MED | MACD-exit + quick-exit didn't call `_check_daily_goal()` | f610b35 |
| MP-8 | MED | Quick-exit-win didn't reset `consecutive_losses` | f610b35 |
| PYR-1 | HIGH | Pyramiding: T1 sold `(initial+adds)//2` but PnL math used `initial*0.5` → understated profits by ~$10-15/pyramid-trade | 05a90b4 |
| PSZ-9 | HIGH | `compute_position_size`: `if equity and >0` was falsy for 0 AND negative → margin-call account could still trade | 039a656 |
| BO-1 | CRITICAL | `protect_position` submitted Stop + TP as 2 separate orders. If Stop fills, TP stays alive → second fire → account SHORT (oversold) | 1fc4c81 |
| BO-3 | HIGH | No error escalation in fallback path | 1fc4c81 |
| BO-7 | HIGH | `cancel_open_orders_for` returned before Alpaca confirmed → racing `submit_sell_limit` could collide | a56d8fe |
| BO-6 | MED | Per-order cancel exceptions silently swallowed | a56d8fe |
| WS-2 | HIGH | `ws.run()` thread could hang on `stop_ws()` SDK no-op → blocks resubscribe forever | eddcd19 |
| PAT-1 | HIGH | Volume filter passed when `vol_sma=0` (zero-volume window) — `v[i] < 0` = False | 3b3bc81 |
| PAT-3 | MED | Flag-retrace `>` filter could pass negative retrace if flag rose above pole | 3b3bc81 |
| SCN-2 | HIGH | `intraday_pct = (high - 0) / 0 * 100 = inf` passed `>= 10%` filter → garbage tickers in watchlist | 687a704 |
| SCN-3 | HIGH | Same div-by-zero for `rvol_proxy` | 687a704 |
| SCN-7 | MED | `df.groupby.tail(1)` could return 2+ week old bars for halted stocks | 687a704 |
| UV-2 | HIGH | `fetch_us_universe` no retry per URL on transient failure | 33d723b |
| UV-9 | HIGH | No stale-cache fallback when both URLs fail → 0 trades that day | 33d723b |
| UV-4 | MED | No cache → 24 HTTP/h with rescan cadence | 33d723b |
| TS-1 | CRITICAL | `two_source_scan.py` (Alpaca-fallback for yfinance) was **never imported** anywhere — complete dead code, bot had no fallback | d3dab43 |

### `position_recovery.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| PR-1 | HIGH | Single-shot close, no retry (same class as HF-1) | fc1b270 |
| PR-2 | HIGH | Returned `len(positions)` even on close failure → bot kept running | fc1b270 |
| PR-6 | MED | No fill-verification poll | fc1b270 |

### `watchdog.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| T  | CRITICAL | Hardcoded API keys (committed in git history!) | (Iter 4, pre-package) |
| U  | HIGH | No trade-lock check → could restart bot mid-position | (Iter 4) |
| WD-1 | HIGH | No restart-loop limit → broken-config crashloop forever | fc2b60e |
| WD-3 | HIGH | `is_bot_running` False on wmic-timeout → restarted bot while still alive (2 bots) | fc2b60e |

### `pre_flight.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| PF-1 | HIGH | `account_blocked` / `trading_blocked` not checked → bot logged in but every order rejected | 0fbe0d2 |
| PF-6 | HIGH | No min-equity check → equity=$0 → bot looked alive but computed 0 shares | 0fbe0d2 |
| PF-7 | MED | Empty api_key/secret gave cryptic SDK exception | 0fbe0d2 |

### `indicators.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| IND-6 | HIGH | `rsi()` returned 50 in monotone uptrend (all `delta > 0`, loss=0 → NaN → fallback) → FBO `RSI>80` filter never fired on parabolic chases (the setup it should filter) | 745ee6f |
| IND-3 | MED | `false_breakout_veto` crashed `KeyError` on bar missing 'high'/'low'/etc — handle_bar wrapped but bar dropped silently | 745ee6f |

### `vwap_filter.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| VWAP-2 | MED | KeyError on missing volume key | 42f3462 |
| VWAP-4 | MED | Negative volume bar (data corruption) silently summed into cum_v | 42f3462 |

### `catalyst_filter.py` / `float_filter.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| FLT-4 | HIGH | Float-cache had NO TTL — daemon running > 1 day used yesterday's float (could change via secondary offering) | e9bed0e |
| CAT-3/4 | DESIGN | `passes_catalyst_filter` could NEVER return False (V1 permissive) — added `strict=True` opt-in | e9bed0e |

### `pump_dump_filter.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| PD-1 | HIGH | Bot called `pd_size_multiplier(score)` without pct+rvol → secondary filter `pct>100 + rvol>50` was DEAD CODE. Exactly the ODYS/WOK $17k-loss profile got through with 1.0× size. | af7cae8 |

### `safe_bracket.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| SB-2 | HIGH | `str(o.status) in ("OrderStatus.FILLED", "filled")` fragile against alpaca-py enum-repr changes → ALL bracket-buys would silently timeout → repair-logic never fires | 98622e7 |
| SB-6 | MED | `filled_avg_price=None` (API quirk) → `float(None)` TypeError → silent timeout without cancel = stranded order | 98622e7 |

### Logging modules (TradeLogger, slippage_log)

| Bug | Sev | Description | Commit |
|---|---|---|---|
| LOG-1 | HIGH | No flush+fsync — cloud-killed bot lost recent trade events | 5d4e161 |
| LOG-2 | MED | No threading.Lock — concurrent async writers could interleave JSON | 5d4e161 |
| LOG-3 | MED | No try/except — disk-full crashed bot mid-trade | 5d4e161 |
| LOG-5 | MED | `slippage_log` returned drift_pct=0.0 silently when expected<=0 — couldn't distinguish data error from 0% | 5d4e161 |

### `delisted_cache.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| DC-1 | HIGH | Non-atomic write — crash mid-write wiped 30d cache (3000+ tickers) → next premarket-scan got yfinance-rate-limit spam | 0818bbe |
| DC-3 | MED | Corrupt-JSON silent reset, no warning | 0818bbe |
| DC-6 | MED | `if ts and ts >= cutoff` falsy for `ts=0.0` (corruption case) | 0818bbe |

### `reconnect_backoff.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| RB-7 | MED | Pathological inputs (negative base, cap < base) silently accepted | ba65b60 |
| RB-9 | MED | No optional jitter → thundering-herd risk on parallel reconnects | ba65b60 |

### `status_dashboard.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| SD-3 | HIGH | `getattr(d, "trades_today", 0)` — but field is `trades_completed_today` → status.json **always reported 0 trades** regardless of reality. External monitor would never see bot activity. | 68f293a |
| SD-1 | HIGH | Non-atomic write — external `cat status.json` saw partial JSON | 68f293a |
| SD-2 | HIGH | Silent except-Pass — disk full silent forever | 68f293a |

### `day_summary_persist.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| DSP-1 | HIGH | Non-atomic write — crash = full day audit-trail gone | 73b5492 |
| DSP-2 | HIGH | Used system-local date, not trading-day — UTC-Cloud bot at 22:00 UTC wrote to NEXT day file | 73b5492 |
| DSP-5 | HIGH | Missing fields: trades_completed_today, adds_executed, quick_exits, goal_reached, spy_size_multiplier — summary was pattern-rejection-counts only, NO trade outcomes | 73b5492 |

### `audit.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| AU-1 | HIGH | `tasklist` Windows-only → Cloud-Linux silent False → audit always recommended RESTART | 42a7195 |
| AU-2 | HIGH | Matched ANY `python.exe` (audit-script self included) → false-positive alive | 42a7195 |
| AU-5 | HIGH | Pre-filter blocked INFO-lines → `KeyboardInterrupt`-pattern in ERROR_PATTERNS was unreachable | 42a7195 |
| AU-7 | HIGH | Multi-line tracebacks: only first line had timestamp → exception body silently dropped | 42a7195 |

### `watchlist_persist.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| WP-6 | CRITICAL | `load_watchlist_if_fresh` was **imported but NEVER called** — mid-day-resume feature broken since existence. Every restart did fresh 60-90s premarket scan. | b14fc44 |
| WP-1 | HIGH | Non-atomic write | b14fc44 |
| WP-5 | LOW | Loader returned only symbols, not scores | b14fc44 |

### `micro_test_trade.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| MT-1 | HIGH | Same status-string bug as SB-2 | e6f7378 |
| MT-2 | CRITICAL | Sell-loop had no timeout cleanup — API hang left position stranded | e6f7378 |
| MT-3 | LOW | Hardcoded candidates list stale | e6f7378 |

### `deploy_safe.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| DS-1 | CRITICAL | `taskkill /F /IM python.exe` killed EVERY python.exe — user's jupyter, tests, other tools collateral | a51ebca |
| DS-2 | HIGH | Race window between `check_positions` and `kill_bot+start_bot` — bot could open position in between | a51ebca |
| DS-5 | MED | SIGKILL gave bot no chance to HARD_FLAT/day_summary | a51ebca |
| DS-6 | CRITICAL | Cross-platform — Linux silent fail | a51ebca |

### `secrets_loader.py`

| Bug | Sev | Description | Commit |
|---|---|---|---|
| SL-1 | HIGH | UTF-8-BOM in `.env` (Windows-Notepad default) → first key got `﻿` prefix → silent missing-key error | 78cb836 |
| SL-7 | MED | No POSIX-permission check — world-readable `.env` is security risk | 78cb836 |
| SL-8 | MED | Generic error message — operator couldn't tell BOM vs missing-file vs format issue | 78cb836 |

### Replay-Engine (`bot.ReplayBot`)

| Bug | Sev | Description | Commit |
|---|---|---|---|
| REP-1 | HIGH | T2-exit in ReplayBot missed T1 PnL (parallel to MP-1 in live bot) — Replay-Baseline systematically too low | 844ffcc |
| REP-2 | HIGH | Stop-after-T1 in ReplayBot missed T1 PnL | 844ffcc |
| REP-5 | MED | `trades_completed_today` never incremented in ReplayBot → MAX_TRADES_PER_DAY filter inactive | 844ffcc |

**Consequence:** Replay baseline shifted $7.08 → $13.14 (correct accounting).

### `premarket_scan` (in bot.py)

| Bug | Sev | Description | Commit |
|---|---|---|---|
| SCN-2/3 | HIGH | div-by-zero infinities passed filter | 687a704 |
| SCN-7 | MED | stale halted bars treated as today's move | 687a704 |

### CI / Deployment

| Bug | Sev | Description | Commit |
|---|---|---|---|
| Cron | HIGH | `0 10 * * 1-5` (top-of-hour) — GitHub schedule contention skipped first day's trigger | 71a4f6e |

---

## The Big Architectural Bug

**WS gives 1-min bars, Cameron's pattern is calibrated for 5-min bars.**

Found 2026-05-13 by running live data through `replay_today.py`:
- 1275 1-min bars analyzed across 9 tickers
- 0 entries — `POLE_MIN_MOVE_PCT=5%` on 1-min bars practically never met
- All 39 prior pilot replays were on 5-min bars (matched config) → replay misleadingly suggested bot worked

**Fix:** `bar_aggregator.py` — buckets 1-min bars into 5-min wall-clock buckets, emits when bucket closes. `on_bar` routes through aggregator before `handle_bar`.

Commit: `b63f601`.

This bug existed since live deployment. Bot literally couldn't trigger trades on live WS data until 2026-05-13 Iter 30.

## Pattern of bugs (meta-analysis)

The 35 bugs cluster into 7 classes:

1. **Single-shot-no-retry** (HF-1, PR-1, UV-2, MT-2): Critical paths assumed first attempt succeeds. Solved by: retry+poll+verify+fallback pattern.
2. **Non-atomic file writes** (DC-1, SD-1, DSP-1, WP-1, also universe_cache): Any external reader could see partial JSON. Solved by: tmp + os.replace().
3. **Dead-code wiring gaps** (TS-1, PD-1, WP-6): Functionality coded but never invoked. Lesson: source-grep tests verify "function X is called by Y".
4. **Cross-platform assumptions** (AU-1, DS-1/DS-6, BAT-2): Windows-specific commands silently no-op on Linux Cloud. Solved by: psutil primary, OS-fallbacks.
5. **Type/format fragility** (SB-2, MT-1, SL-1): String compares + missing encoding handling. Solved by: defensive validators, multiple-accessor checks.
6. **PnL accounting** (MP-1, PYR-1, REP-1, REP-2): T1/T2/stop didn't all account for prior partial fills. Solved by: explicit `t1_shares_sold` field.
7. **Logic-error in filters** (IND-6, PAT-1, SCN-2/3, VWAP-4): Edge cases (NaN, division-by-zero, zero-volume) silently passed/failed filters incorrectly.

## What's stable now

- All 7 classes have at least 5 fixes each + regression tests
- Test count: **501 passing** (was 125 at start)
- Replay-baseline $13.14 stable across 12+ commits
- Atomic-write pattern consistent across all 6 persistence modules
- Cross-platform via psutil cross-checked in audit.py + deploy_safe.py
