# OPERATIONS_RUNBOOK — Cameron-Bot

Operator-facing steps for running, restarting, and diagnosing the live
Cameron bull-flag bot. Companion to the source-level README; this file
is about *running* the bot, not *reading* it.

> **Phase-13 (ChatGPT-20:11):** authored when the Phase-12 watchdog
> rewrite was on disk but the OLD watchdog process was still alive in
> memory, logging `No module named 'alpaca'` every 5 minutes.
> Reading this doc and executing it is what makes Phase-12 actually take
> effect at runtime.

---

## 0. TL;DR for "watchdog is spamming alpaca errors"

```powershell
# 1) kill the stale watchdog
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%watchdog.py%'" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2) optional but recommended: point BOT_PYTHON at a venv with deps
$env:BOT_PYTHON = "C:\Users\Szymon\ross-cameron\.venv\Scripts\python.exe"

# 3) verify the resolved Python actually has the deps
& $env:BOT_PYTHON 06_live_bot\watchdog.py --preflight-only

# 4) launch the new watchdog
.\06_live_bot\run_watchdog.ps1
```

After step 4, `06_live_bot/watchdog.log` should show:
```
Bot-Python resolved -> ...
Preflight OK -> deps importable: ('alpaca', 'yfinance', 'pandas', 'pyarrow')
```
and **no** `No module named 'alpaca'` errors.

---

## 1. Required runtime dependencies

The bot Python (`BOT_PYTHON`, or the resolved fallback) MUST be able to
import all of:

| Module     | Why                                                      |
|------------|----------------------------------------------------------|
| `alpaca`   | Live broker — order submission, position check           |
| `yfinance` | Catalyst-news + SPY trend pre-scan                       |
| `pandas`   | Bar / scanner dataframes, pilot parquet                  |
| `pyarrow`  | Parquet read for pilot backtest and live cache restore   |

The watchdog runs `preflight_dependencies()` once at startup; if any
of these are missing, the watchdog exits with an operator-action message
and **does not enter the restart loop**. This is the Phase-12 fix for the
old "restart-spam every 5 min" behavior.

---

## 2. Detect a stale or wrong-Python watchdog

```powershell
# Show all python processes and their command lines
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Select-Object ProcessId,CommandLine | Format-Table -Wrap
```

A stale watchdog looks like:
```
ProcessId  CommandLine
---------  -----------
  12345    python watchdog.py
```
i.e. the unqualified `python`, not the bot venv's Python.

You can also inspect the tail of `06_live_bot/watchdog.log`:
```powershell
Get-Content 06_live_bot\watchdog.log -Tail 20
```

Symptoms of "old watchdog still running":
- Every 5 minutes a line like
  `ERROR Watchdog: position-check failed - abort restart: No module named 'alpaca'`
- `bot_daemon_alive: false` from `python 06_live_bot/no_trade_postmortem.py`
- `status.json` stale by >30 min (`status_json_stale_seconds > 1800`)

---

## 3. Stop the stale watchdog

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%watchdog.py%'" |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Confirm no python.exe is still running `watchdog.py`:
```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%watchdog.py%'"
```
(should return nothing)

---

## 4. Create (or repair) the bot venv

Only needed on first setup, or after Python upgrades.

```powershell
cd C:\Users\Szymon\ross-cameron
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Verify:
```powershell
$env:BOT_PYTHON = "C:\Users\Szymon\ross-cameron\.venv\Scripts\python.exe"
& $env:BOT_PYTHON 06_live_bot\watchdog.py --preflight-only
```
Expected output:
```
Bot-Python resolved -> C:\Users\Szymon\ross-cameron\.venv\Scripts\python.exe
Preflight OK -> deps importable: ('alpaca', 'yfinance', 'pandas', 'pyarrow')
```

---

## 5. Launch the watchdog (production entrypoint)

```powershell
cd C:\Users\Szymon\ross-cameron
.\06_live_bot\run_watchdog.ps1
```

`run_watchdog.ps1` resolves `BOT_PYTHON` in this priority order:
1. `$env:BOT_PYTHON` if set and the file exists
2. `<repo>\.venv\Scripts\python.exe`
3. `<repo>\06_live_bot\.venv\Scripts\python.exe`
4. `python` on PATH (last resort — will likely fail preflight)

The watchdog then:
1. Runs `preflight_dependencies(BOT_PYTHON)`. If anything missing, prints
   the operator-action block and **exits cleanly**.
2. Loops every 5 minutes:
   - Calls `is_bot_running()`. On `CheckUnknown` → skip the cycle.
   - If alive → log `Bot OK`.
   - If dead → call `start_bot(BOT_PYTHON)` which:
     - reads Alpaca keys via `secrets_loader`
     - runs the position-check in a subprocess of `BOT_PYTHON`
     - if positions exist → block restart (don't flatten by restarting)
     - if no positions → spawn `bot.py --daemon` in a new process group
   - Crashloop protection: after 5 restarts/hour, watchdog exits.

---

## 6. Verify the watchdog is healthy

```powershell
Get-Content 06_live_bot\watchdog.log -Tail 30
```

Healthy log:
```
2026-05-14 22:45:00 INFO  WATCHDOG START - checks every 300 sec (max 5 restarts/h)
2026-05-14 22:45:00 INFO  Bot-Python resolved -> C:\...\.venv\Scripts\python.exe
2026-05-14 22:45:01 INFO  Preflight OK - deps importable: ('alpaca', 'yfinance', 'pandas', 'pyarrow')
2026-05-14 22:45:01 INFO  ============================================================
2026-05-14 22:50:01 INFO  Bot OK - PIDs [54321]
```

---

## 7. No-trade-day post-mortem

After a session where the bot made zero trades, run:

```powershell
& $env:BOT_PYTHON 06_live_bot\no_trade_postmortem.py
# OR for a specific date:
& $env:BOT_PYTHON 06_live_bot\no_trade_postmortem.py 2026-05-14
```

Writes `06_live_bot/no_trade_postmortem_YYYYMMDD.json` with:
- `bot_daemon_alive`, `watchdog_alive` + their PIDs
- `last_watchdog_error`, `last_bot_start`, `last_ws_subscription`
- `status_json_ts`, `status_json_stale_seconds`
- `pre_rank_candidates`, `watchlist`, `pattern_reject_counts`
- `orders_submitted`
- `final_reason_no_trade` — single actionable line, e.g.
  - `"bot_daemon_dead; watchdog last error: No module named 'alpaca'"`
  - `"bot_daemon_alive, scan produced 0 pre-rank candidates"`
  - `"orders_submitted=2 (NOT a no-trade day)"`

Use `--json` to print to stdout instead of writing the file.

---

## 8. Common failure modes & fixes

| Symptom | Root cause | Fix |
|---|---|---|
| `No module named 'alpaca'` every 5 min in watchdog.log | Old watchdog running with wrong Python | §3 + §5 |
| Watchdog log says `BLOCKED restart: N positions open` | Bot crashed with live positions — restart-recovery would flatten them | Manually flatten the open positions in Alpaca UI, then watchdog will restart cleanly |
| `position-check failed -- abort restart (no double-launch)` | BOT_PYTHON valid but Alpaca creds missing/wrong | Check `secrets_loader` / `.env` / `APCA_API_KEY_ID` env var |
| `status.json` stale > 1h despite watchdog running | Bot daemon hung (rare). | Kill the daemon PID, watchdog will restart it. |
| `CRASHLOOP DETECTED` in watchdog.log | Bot dies on launch >5x/hr | Read `06_live_bot/daemon.log`; usually a config or credential bug |

---

## 9. Quick reference: relevant scripts

| Path | Purpose |
|---|---|
| `06_live_bot/bot.py` | The bot itself. `--daemon` = production. |
| `06_live_bot/watchdog.py` | Process supervisor + restart logic. `--preflight-only` = dep check & exit. |
| `06_live_bot/run_watchdog.ps1` | Operator entrypoint — resolves BOT_PYTHON and starts watchdog. |
| `06_live_bot/no_trade_postmortem.py` | Diagnostic CLI for no-trade days. |
| `06_live_bot/status.json` | Latest bot status snapshot (written by daemon). |
| `06_live_bot/daemon.log` | Bot daemon stdout/stderr. |
| `06_live_bot/watchdog.log` | Watchdog actions. |
| `06_live_bot/trades_live.jsonl` | Live trade ledger. **Never** mixed with replay events (Phase-11). |
| `06_live_bot/trades_replay.jsonl` | Replay-bot events (separated from live ledger in Phase-11). |
