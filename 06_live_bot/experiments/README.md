# 06_live_bot/experiments/

Phase-21 (ChatGPT-09:15 Task 4): these are **experiment scripts**, not
pytest tests. Despite the `test_*.py` filename, they are not collected
by pytest — see `pytest.ini` `norecursedirs` and `testpaths = tests`.

Each script is a backtest / parameter sweep wrapper. They take command-
line args, print results, and exit. They are NOT regression tests and
should NOT be confused with the test suite in `../../tests/`.

Run an individual experiment:

    python 06_live_bot/experiments/test_pole_min.py
    python 06_live_bot/experiments/test_max_risk_pct.py

Catalog (post-Iter-N trader-loop history):

| Script | Purpose |
|---|---|
| `test_adaptive_qe.py` | Adaptive QE-threshold sweep |
| `test_breakout_vol.py` | BREAKOUT_VOL_FACTOR sweep |
| `test_entry_start_time.py` | TIME_NEW_ENTRIES_START sweep |
| `test_entry_window.py` | Late-window entry tests |
| `test_flag_retrace.py` | FLAG_RETRACE_MAX_PCT sweep |
| `test_max_pole_r.py` | MAX_POLE_T2_R sweep |
| `test_max_risk_pct.py` | MAX_RISK_PCT sweep |
| `test_one_loss_stop.py` | Single-loss-stop trader-rule |
| `test_pole_min.py` | POLE_MIN_MOVE_PCT sweep |
| `test_pole_vol_rising.py` | Pole-volume-rising filter |
| `test_power_hour_size.py` | Time-of-day size multiplier |
| `test_pullback_limit.py` | MAX_PULLBACKS_PER_DAY |
| `test_quick_exit.py` | QUICK_EXIT_THRESHOLD_CENTS sweep (Iter 5: 30c→20c COMMIT) |
| `test_spy_veto.py` | SPY trend veto |
| `test_t1_stop_lockin.py` | T1-stop-lock-in behavior |
| `test_t2_definition.py` | T2 R-multiple vs pole-height |
| `test_time_exit.py` | TIME_EXIT_PRE_HARD_FLAT |
| `test_top_rank_only.py` | Top-rank candidate filter |
| `test_trailing_post_t1.py` | Trailing stop after T1 (Iter 4: SKIP) |

For real tests, see `../../tests/`.
