# Agent Brain Snapshot — Working Memory & Operating Principles

This is the agent (me) that built the package, dumping its operating state and
patterns for the reviewing agent's context.

## Self-imposed rules during this audit (Iter 1-35)

1. **One module per iteration** — don't fan out to unrelated changes
2. **Reproduce before fixing** — every bug claim has a reproduction snippet
3. **Tests first** — every fix gets a regression test that would have caught it
4. **No silent tuning** — strategy changes only with backtest evidence
5. **Atomic writes** — any state file gets tmp+rename pattern
6. **Cross-platform** — Windows fallbacks for Linux Cloud
7. **Honest commits** — message includes severity, reproduction, fix scope
8. **Source-grep regressions** — for wiring bugs (function-X-calls-Y) where
   behavior tests don't naturally cover, add a source-grep test
9. **No magic numbers** — every threshold gets a constant + comment
10. **`getattr` defaults** for backwards-compat on extending dataclasses

## Bug-class taxonomy (learned during audit)

When investigating a new module, look for:

### Class 1: Single-shot critical paths
Pattern: `try: X() except: log` where X is critical (positions, orders, state).
Fix: retry loop with poll-verify + per-item fallback.
Examples: HF-1 (market_close_all), PR-1 (recover_or_flatten), UV-2 (universe).

### Class 2: Non-atomic file writes
Pattern: `path.write_text(json.dumps(...))` for any state file.
Fix: `tmp = path.with_suffix(".tmp"); tmp.write_text(...); os.replace(tmp, path)`.
Examples: DC-1, SD-1, DSP-1, WP-1, universe_cache.

### Class 3: Dead wiring
Pattern: function defined + imported but never called.
Fix: actually call it where intended; add source-grep test.
Examples: TS-1 (two_source_scan), PD-1 (pump-dump full filter), WP-6 (load_watchlist).

### Class 4: Cross-platform assumptions
Pattern: Windows-specific commands (`taskkill`, `wmic`, `tasklist`).
Fix: psutil primary, OS-fallback secondary.
Examples: AU-1, DS-1, DS-6.

### Class 5: Type/encoding fragility
Pattern: `str(enum_value)` compares, `read_text(encoding="utf-8")` without -sig.
Fix: multi-accessor checks (`.value`, `.name`, `str()`), encoding="utf-8-sig".
Examples: SB-2, MT-1, SL-1.

### Class 6: PnL accounting
Pattern: `pnl = (X - entry) * shares` ignoring prior partial fills.
Fix: explicit field tracking what was sold at each level (e.g. `t1_shares_sold`).
Examples: MP-1, PYR-1, REP-1, REP-2.

### Class 7: Filter logic on edge cases
Pattern: `if val > threshold:` where val can be NaN/inf/negative due to data.
Fix: defensive guards BEFORE the comparison (`np.isfinite`, value ranges).
Examples: IND-6 (RSI), PAT-1 (vol_sma=0), SCN-2 (prev_close=0), VWAP-4 (neg vol).

## Patterns I deliberately used

### Async-cooperative state
The bot is single-asyncio-event-loop. Between awaits, code is effectively
atomic. So:
- No locks needed for ticker state mutations in `intraday_rescan` and
  `manage_position` (no awaits inside their dict-mutation regions)
- Threading.Lock only on FILE writes where multiple coroutines might race
  to flush JSON

### Trading-day vs system-day distinction
Cloud runs UTC. Trading is NY-based. Any "today" check uses:
- `DayState.date` (set at bot init from trading-day context)
- `datetime.now(NY_TZ).date()` if explicitly NY
- Never raw `datetime.now()` (system local) for trading-day decisions

### Defense in depth
For critical paths, multiple layers:
- pre_flight checks min-equity → first gate
- compute_position_size also checks equity > 0 → second gate
- Same for blocked-account: pre_flight + Alpaca-side rejection

### Backwards-compat extension
When adding new DayState/TickerState fields:
- New field has default value
- All readers use `getattr(obj, "field", default)` for one cycle
- Old tests that built minimal mocks still pass

## My uncertainties (be sceptical about)

1. **Replay-Live divergence.** I'm confident ReplayBot now mirrors live for
   T1/T2/stop accounting. But ReplayBot still doesn't include MACD-exit,
   quick-exit, pyramiding. Backtest results are LOWER-bound estimates of
   live performance.

2. **Cameron-fidelity in finer detail.** I implemented based on Cameron's
   public videos. Internal rules (e.g. exact size of T2-stretch, exact
   pyramid trigger semantics) might differ from what I encoded.

3. **The 0-trades-today bug.** I fixed both root causes (cron + timeframe)
   but haven't validated end-to-end live yet. Tomorrow's behavior is still
   unproven.

4. **Test coverage of async race conditions.** I added tests for atomic
   operations + cancel-confirmation + ws_task cleanup. But async-race
   coverage is fundamentally hard. May have residual races I missed.

5. **The hardcoded keys in .bat files.** Found Iter 32, was about to fix
   but user redirected to package this for review instead. **STILL UNFIXED
   AS OF PACKAGING.** Keys are at start_bot.bat:8-9, start_all.bat:8-9,
   start_watchdog.bat:4-5. Paper-account keys so no immediate $ risk,
   but user should rotate them.

## Working memory / context I'm tracking

- **Production config:** POLE 5, TOPPING 0.4, RETRACE 50, VOL 1.5x
  (unchanged through audit despite tempting backtest data on TOPPING 0.5)
- **Replay baseline:** $13.14 (anchored test_replay_regression.py)
- **Live track record:** 2026-05-12 first paper-live day (TSLA/AAPL/GOOGL
  trio + ATRA + HSPT), some trades fired. 2026-05-13: 0 trades (cron +
  timeframe bugs).
- **Cloud deployment:** GitHub-Actions Mo-Fr 09:47 UTC, public repo for
  unlimited Actions minutes, secrets via gh-cli.
- **Open decision:** Option B (TOPPING 0.4→0.5) — user has not chosen.

## How I think about this codebase

Cameron's strategy is **opinionated and selective by design**. The bot
SHOULD trade 1-3 times/day, not 10. SHOULD have 60-70% win-rate, not 90%.
SHOULD have $5-10/trade avg PnL on paper, scaling to $20-50 on live with
larger position sizes.

If a "fix" makes the bot trade MORE, that's usually wrong (Cameron's #1
beginner mistake is overtrading). If a fix makes the bot trade LESS but
each trade is higher quality, that's usually right.

The audit was driven by this principle. I rejected "looser filters" tunes
when backtest showed only marginal PnL gain at higher noise cost.

## What I'd want to verify if I were the reviewer

1. Run `compare_retrace_threshold.py` on a longer date range — does
   `higher-topping` advantage hold beyond 39 days?
2. Run live paper trading with current config for 30 sessions, measure
   actual vs backtest divergence
3. Implement strict-mode for catalyst/VWAP and backtest impact
4. Examine the live failure logs from 2026-05-11 (WS bug) and 2026-05-12
   (HSPT stale-price) to find any similar latent issues
5. Verify alpaca-py SDK version compatibility — the `_status_is` defensive
   helper was added because SDK enum reprs aren't stable across versions
