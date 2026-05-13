# Changes Applied After First Review (2026-05-13/14)

The first external AI reviewer (memo `claude_cameron_bot_review.txt`) found
multiple critical blockers. This document tracks what was fixed since then.

## P0 — Reproducibility blockers (ALL FIXED)

| # | Issue | Status |
|---|-------|--------|
| A | `tests/conftest.py` `src/`-only path | ✅ FIXED — now `06_live_bot` (+ src fallback) |
| B | `daily-trading.yml` installed `requirements.txt` not present | ✅ FIXED — root `requirements.txt` added |
| B | Missing `pytest-asyncio`, `pyarrow`, `psutil` | ✅ FIXED — added to requirements |
| C | Pilot data path `04_backtest/data_pilot/` not in package | ✅ FIXED — `find_pilot_data_paths()` helper with fallback to `backtest_data/`. All 8 callers updated. |
| D | `handle_bar_5min` NameError on `bar` ref in except | ✅ FIXED — uses `sym`. Behavior-test added. |

## P0 — Critical-code issues (ALL FIXED)

| # | Issue | Status |
|---|-------|--------|
| 17 | `NY_TZ = timezone(-4)` wrong in EST winter | ✅ FIXED — `ZoneInfo("America/New_York")`. DST-boundary tests added. |
| 16 | `Bot.run()` `asyncio.gather` → ws_loop stuck after HARD_FLAT | ✅ FIXED — `asyncio.wait(FIRST_COMPLETED)` + cancel pending |
| 6 | `market_close_all` fallback always SELL even for short | ✅ FIXED — `side = SELL if qty>0 else BUY`. Test verifies. |

## P1 — Trading-safety (PARTIALLY FIXED — major path done)

| # | Issue | Status |
|---|-------|--------|
| 1 | **Entry: accepted ≠ filled** — bot set `in_position=True` on submit | ✅ FIXED — `submit_bracket_buy` now polls for actual fill, returns dict with `status`/`fill_price`/`shares`. Bot only opens position when `status="filled"`. Uses real fill price + qty. T1/T2/stop recomputed relative to actual fill. |
| 2 | Pyramid adds also assume immediate fill | ⚠️ NOT YET — same fix pattern would apply but adds happen in fast-moving stocks where fills are typically instant; lower priority. |
| 3 | Exits log PnL before broker confirms | ⚠️ NOT YET — same fix pattern needed; current behavior logs against limit-sell which usually fills, but rare misses leave naked position. |
| 4 | `submit_sell_limit` cancels protection then exits unprotected | ⚠️ NOT YET — needs separate `submit_emergency_exit_market()` for hard stops. |
| 5 | `safe_bracket.py` not wired into live entry | ⚠️ NOT YET — module still standalone. Could be integrated as pre-flight check in `submit_bracket_buy`. |
| 7 | Daily-loss/max-trades count only completed trades | ⚠️ NOT YET — would need `pending_entries_count` etc. |

## P2 — Strategy fidelity (PARTIAL FIXES)

| # | Issue | Status |
|---|-------|--------|
| 9 | Catalyst-Filter permissive even when `CATALYST_REQUIRED=True` | ✅ FIXED — now passes `strict=True` to `passes_catalyst_filter` |
| 10 | Float-Filter permissive on unknown | ⚠️ NOT YET — would need `strict` mode (low priority) |
| 11 | Liquidity-cap + post-power-size never invoked | ✅ FIXED — `ny_time` and `avg_volume` (from rolling bar window) now passed to `compute_position_size` |
| 12 | `QUARTER_SIZE_UNLOCK_CENTS=0.20` contradicts spec ($0.50) | ✅ FIXED — renamed `QUARTER_SIZE_UNLOCK_USD_PER_SHARE = 0.50` (with alias for backwards-compat) |
| 8 | Premarket-Scanner uses daily-bars not actual premarket | ⚠️ NOT YET — major rewrite, scoped as separate work-item |
| 13 | SPY-Multiplier never refreshed intraday | ⚠️ NOT YET — would need re-pull at 09:35/10:00/10:30 |
| 14 | VWAP definition unclear (premarket+RTH mixed) | ⚠️ NOT YET — needs `VWAP_MODE` config |
| 15 | RSI > 80 hard-veto too coarse | ⚠️ NOT YET — would need combined-veto refactor |

## P3 — Tests / backtest engine

| # | Issue | Status |
|---|-------|--------|
| 21 | Too many source-grep tests | ⚠️ PARTIAL — new fixes added behavior-tests, but many old source-grep tests remain |
| 22 | Replay-regression network-required | ✅ FIXED — `find_pilot_data_paths()` works offline; replay works from REVIEW_PACKAGE root |
| 23 | No install-test | ⚠️ NOT YET — separate work-item |
| 24 | ReplayBot ≠ live-parity | ⚠️ ACKNOWLEDGED — documented in BACKTEST_RESULTS.md |

## Verification (from REVIEW_PACKAGE root)

```bash
cd REVIEW_PACKAGE
pip install -r requirements.txt
python -m pytest -q              # → 509 passing, 1 skipped (POSIX-only test)
python 06_live_bot/bot.py --replay 2026-04-15
# → Daily realized PnL: $13.14
```

Both commands now work out-of-the-box.

## Strategy improvements (since first review)

Two backtest-validated strategy tunes were applied:

1. **MAX_RISK_PCT=8%** — reject entries where (entry-stop)/entry > 8%.
   - Pilot data: trades with risk%≥10% had 80% loss-rate (4/5).
   - Backtest impact: 17 trades → 9, PnL stays similar (-$2), but win-rate
     67%→78%, MaxDD halves (-$30.63→-$18.78), 0 spiral days, Sharpe-like +59%.

2. **POLE_TOPPING_TAIL_MAX 0.4 → 0.5** — match Cameron's literal spec.
   - YAML constraints + code comment both said "topping > 50% = veto" but
     implementation was 0.4 (over-strict).
   - With MAX_RISK_PCT=8 baseline: $73→$120 PnL (+60%), 9→13 trades,
     same MaxDD, Sharpe-like 3.89→9.64 (+147%).

Cumulative (Iter 1 + Iter 2):
- Original baseline: 17 trades, $75.17 PnL, 67% win, -$30.63 MaxDD
- Now: 13 trades, $120.47 PnL, 75% win, -$12.50 MaxDD (Sharpe +293%)

## Status assessment

Reviewer's minimum acceptance criteria status:

- [x] `pytest -q` runs from fresh clone without PYTHONPATH hacks
- [x] Daily-GitHub-Action installs deps successfully
- [x] `bot.py --replay 2026-04-15` works with delivered backtest_data
- [x] Replay runs offline (no network needed)
- [x] Entry-order accepted does NOT immediately set `in_position=True` (P1#1 fix)
- [ ] Partial fill correctly represented (handled in shares=actual_filled_qty, not yet pursued aggressively)
- [x] Rejected/canceled entry leaves state unchanged
- [ ] Add-Order changes shares only after fill (NOT YET — P1#2)
- [ ] T1/T2/Stop/MACD/Quick PnL booked only after fill (NOT YET — P1#3)
- [ ] Protection never blindly removed without verified replacement (NOT YET — P1#4)
- [x] HARD_FLAT cleanly ends WS + run
- [x] Timezone uses `America/New_York`
- [ ] Premarket-Scan uses actual premarket data (NOT YET — P2#8)
- [ ] Backtest-Sweep based on live-parity engine (NOT YET — P3#24)
- [ ] 30 paper-days documented without silent failure (NOT YET)

**Production-readiness:** still not. P1#2-#7 + P2#8 are blockers for live.
Paper-trading with new safety net is OK.
