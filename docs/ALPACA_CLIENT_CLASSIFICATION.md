# Raw-Alpaca-Client Classification (Phase-71, 2026-05-18)

ChatGPT 20260517_2233 P5: "Claude soll eine Tabelle pflegen: Datei,
Alpaca-Call-Typ, live-parallel moeglich ja/nein, guarded ja/nein,
Begruendung."

Every direct `TradingClient(...)`, `StockHistoricalDataClient(...)`,
or `StockDataStream(...)` constructor in `06_live_bot/`. Result of
`grep -rnE "TradingClient\(|StockHistoricalDataClient\(|StockDataStream\("`.

## Live-parallel-safe (guarded or never-runs-during-trading)

| File | Line | Client | Live-Parallel? | Guarded? | Why |
|------|------|--------|----------------|----------|-----|
| `bot.py` | 2290 | `StockDataStream(IEX)` | **YES (this IS the live bot)** | ✅ singleton via `alpaca_ws_patch` Phase-43 + atomic-lockfile Phase-65 | The one and only live WS instance |
| `guarded_alpaca.py` | 250 | `TradingClient(...)` inside `GuardedTradingClient` factory | yes (proxied) | ✅ inherently — it IS the guarded wrapper | All live REST goes through here |
| `guarded_alpaca.py` | 259 | `StockHistoricalDataClient(...)` inside `GuardedStockHistoricalDataClient` factory | yes (proxied) | ✅ same as above | All live data REST goes through here |
| `force_trade_loop.py` | 71-72 | `GuardedTradingClient` + `GuardedStockHistoricalDataClient` (preferred) | **runs only with `--i-understand-paper-demo` (Phase-71)** | ✅ guarded path is the default | Paper-demo tool, gated by safety flag |
| `force_trade_loop.py` | 76-77 | `TradingClient(paper=True)` + `StockHistoricalDataClient(...)` fallback | (same gate as above) | ❌ raw fallback only used when guarded import fails | Last-resort fallback; safety banner refuses to start without flag |
| `pre_flight.py` | 63 | `StockDataStream(IEX)` | **NO — only runs at bot startup, before main WS connection** | ❌ raw | Single short-lived auth probe; closes before `bot.py` connects its own WS |
| `watchdog.py` | 152 | `TradingClient(paper=True)` inside subprocess code-string | **YES (watchdog runs continuously)** but only when checking positions before restart | ❌ raw subprocess | Short-lived child process, ~5 sec total per check, ~once every 5 min. Not parallel with bot's main REST burst because watchdog checks AFTER bot has died. |
| `alpaca_rate_guard.py` | 181 | `StockDataStream(IEX)` inside `_stall_probe()` | **NO — separate WS slot for liveness probe** | ❌ raw (probe-only) | Phase-38 documents that the probe self-blocked the slot; the probe code was REMOVED from the live stall-recovery path. Function exists but is dead code; needs cleanup. |

## Operator / tool-only (never runs during automated trading)

| File | Line | Client | Why operator-only |
|------|------|--------|-------------------|
| `deploy_safe.py` | 41 | `TradingClient(paper=True)` | Operator deploy-check script; never auto-runs |
| `micro_test_trade.py` | 50-51 | `TradingClient` + `StockHistoricalDataClient` | Manual sanity-test for $1 bracket order; operator-invoked only |
| `tools/live_readiness_check.py` | 62, 84 | `TradingClient` + `StockHistoricalDataClient` | Pre-flight summary script; operator-invoked only |
| `tools/morning_check.py` | 20 | `TradingClient(paper=True)` | Operator morning-routine: shows equity, open positions |
| `tools/movers_now.py` | 25 | `StockHistoricalDataClient` | Operator-invoked screen of top gappers right now |
| `tools/pos_check.py` | 13-14 | `TradingClient` + `StockHistoricalDataClient` | Operator-invoked positions snapshot |
| `experiments/test_spy_veto.py` | 37 | `StockHistoricalDataClient` | Experimental SPY-filter analysis script; not part of live flow |
| `backtest_day.py` | 31 | `StockHistoricalDataClient` | Historical-bar download for backtests; runs offline |

## Documentation/test-only (no live impact)

`alpaca_ws_patch.py:66,280,332`: comments + log messages, not actual
constructors. The actual patching is via class-level `__new__` /
`__init__` overrides — see lines 240-310.

## Recommendation

**Live-parallel risk is contained.** The only live-concurrent
`TradingClient(...)` raw construction is in `watchdog.py:152`, which:
- Runs in a separate Python subprocess (not the bot's process)
- Only runs when bot.py is DEAD (about to restart)
- Total HTTP requests per check: 1 (positions)
- Phase-62 PID-lockfile prevents two bots starting simultaneously

The remaining raw clients are all operator-tools that humans invoke
manually and DON'T run alongside the live bot daemon.

**Action items:**

1. ✅ `force_trade_loop.py` — Phase-71 added `--i-understand-paper-demo`
   gate + paper-key prefix check.
2. ⚠️ `alpaca_rate_guard.py:181` `_stall_probe` — dead code per Phase-38
   comments. Should be deleted to reduce surface area.
3. ⚠️ `watchdog.py:152` could be migrated to `GuardedTradingClient` but
   it lives in a subprocess that may not have `06_live_bot` on
   sys.path — would need careful refactor.
4. Operator tools (`tools/*.py`, `deploy_safe.py`, `micro_test_trade.py`)
   are accepted-as-is per Phase-57 audit: they are NOT live-parallel.

## Verification

```bash
# Find all current raw-client sites
grep -rnE "TradingClient\(|StockHistoricalDataClient\(|StockDataStream\(" \
    06_live_bot --include="*.py" | grep -v "^Binary"

# Live-parallel paths (sub-set of above):
# - bot.py (main daemon)
# - watchdog.py subprocess (positions check)
# - pre_flight.py (startup probe)
# - guarded_alpaca.py (the wrapper itself)
```

Anyone touching the live trading path should consult this table and
add their new constructor here.
