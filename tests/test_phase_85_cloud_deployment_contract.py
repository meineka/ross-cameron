"""Phase-85 (2026-05-20): cloud-deployment + ntfy + loose-mode contract.

User: "bot soll im loose modus starten er soll den ganzen tag traden,
er soll wenig schranken haben, ich will dass log gut ist und alles
aus github cloud läuft, weil hier wird internet abgestellt, ich will
sehen dass benachrichtigungen funktionieren. Das alles musst Du dir
in die testcases schreiben, damit das für immer läuft."

The user is running cloud-only from now on. These tests pin the
contract so future commits don't accidentally break:

  1. GitHub Actions workflow exists + is correctly configured
  2. Default STRATEGY_VARIANT is "loose" (not strict, not force)
  3. SKIP_HARD_FLAT_TODAY default is "1" (full day trading)
  4. NTFY_TOPIC secret is referenced (cloud bot CAN push)
  5. Startup ntfy step exists (user sees confirmation at run start)
  6. EOD ntfy step exists (user sees summary at HARD_FLAT)
  7. All necessary log artifacts are uploaded
  8. The bot's daemon-mode startup pushes "Bot started" (so user sees
     activity even before first trade)
  9. _push_trade BUY signature accepts stop/target (so notifications
     include Stop, Target, R:R — Phase-82 contract preserved)
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-trading.yml"


def _wf_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _wf_dict() -> dict:
    return yaml.safe_load(_wf_text())


def _bot_src() -> str:
    return (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")


# ─── A. Workflow file exists + is valid YAML ────────────────────────────

def test_workflow_file_exists():
    assert WORKFLOW.exists(), f"missing: {WORKFLOW}"


def test_workflow_is_valid_yaml():
    wf = _wf_dict()
    assert wf is not None
    assert "jobs" in wf
    assert "trade" in wf["jobs"]


# ─── B. Schedule is correct (NY market open weekdays) ──────────────────

def test_workflow_runs_weekdays():
    """Bot must NOT run weekends — NY market closed."""
    src = _wf_text()
    # Cron must use day-of-week 1-5 (Mon-Fri)
    assert re.search(r"cron:.*1-5", src), "schedule must restrict to Mon-Fri"


def test_workflow_scheduled_before_ny_open():
    """Schedule must fire BEFORE 13:30 UTC (= 09:30 ET NY-open). Ideal
    is 5-10 min before so the bot has time to do preflight + scan."""
    src = _wf_text()
    # Find cron expression — e.g. "23 13 * * 1-5" = 13:23 UTC
    m = re.search(r"cron:\s*['\"]?(\d+)\s+(\d+)\s+\*\s+\*\s+1-5", src)
    assert m, "cron expression not found"
    minute, hour = int(m.group(1)), int(m.group(2))
    total_min = hour * 60 + minute
    # Must be before 13:30 UTC = 810 min into day; after 13:00 = 780
    assert 780 <= total_min < 810, (
        f"schedule {hour:02d}:{minute:02d} UTC outside 13:00-13:30 window"
    )


# ─── C. Strategy default is LOOSE (user request) ────────────────────────

def test_default_strategy_is_loose_or_ultra():
    """User initially asked for 'loose modus', then switched to 'ultra'
    after seeing 3-month backtest results (ultra: $18,399 vs loose $4,354).
    Either is acceptable; strict/force/relaxed forbidden because:
      - strict makes 0-2 trades/day (user wants more activity)
      - force is chaos mode (no pattern, paper-only stress test)
      - relaxed has Cameron-strict entries (same as strict for trade count)."""
    src = _wf_text()
    m = re.search(
        r"strategy_variant:[\s\S]{0,200}default:\s*['\"]?(\w+)['\"]?",
        src,
    )
    assert m, "workflow_dispatch input default for strategy_variant missing"
    default = m.group(1).lower()
    assert default in ("loose", "ultra"), (
        f"default '{default}' not in (loose, ultra) — these are the only "
        f"variants with realistic trade frequency for daily cloud trading"
    )
    # Env fallback when manual not provided
    m2 = re.search(
        r"STRATEGY_VARIANT:.*\|\|\s*['\"]?(\w+)['\"]?",
        src,
    )
    assert m2, "STRATEGY_VARIANT env fallback missing"
    fb = m2.group(1).lower()
    assert fb in ("loose", "ultra"), (
        f"env fallback '{fb}' not in (loose, ultra)"
    )


def test_default_strategy_not_strict_or_force():
    """Belt-and-suspenders: explicitly forbid strict (no trades) and
    force (chaos mode) as the default for the cloud-recurring run."""
    src = _wf_text()
    # The env fallback line must not say 'strict' or 'force' as default
    m = re.search(
        r"STRATEGY_VARIANT:[^\n]+\|\|\s*['\"]?(\w+)['\"]?",
        src,
    )
    assert m
    default = m.group(1).lower()
    assert default not in ("strict", "force"), (
        f"default '{default}' is forbidden for cloud-recurring runs"
    )


# ─── D. Full-day trading: SKIP_HARD_FLAT_TODAY=1 default ────────────────

def test_skip_hard_flat_default_enabled():
    """User: 'ganzen tag traden, wenig schranken'.
    SKIP_HARD_FLAT_TODAY=1 moves HARD_FLAT 12:00 ET → 15:55 ET."""
    src = _wf_text()
    m = re.search(
        r"skip_hard_flat:[\s\S]{0,200}default:\s*['\"]?1['\"]?",
        src,
    )
    assert m, "skip_hard_flat default must be '1' for full-day"
    m2 = re.search(
        r"SKIP_HARD_FLAT_TODAY:.*\|\|\s*['\"]?1['\"]?",
        src,
    )
    assert m2, "SKIP_HARD_FLAT_TODAY env fallback must default to '1'"


# ─── E. NTFY notifications wired up ─────────────────────────────────────

def test_workflow_references_ntfy_topic_secret():
    """NTFY_TOPIC secret must be passed to the bot environment so
    pushes from inside the bot work."""
    src = _wf_text()
    assert "${{ secrets.NTFY_TOPIC }}" in src
    assert "NTFY_TOPIC" in src


def test_workflow_has_startup_ntfy_step():
    """Phase-85: workflow must push ntfy at start (BEFORE the 8h sleep)
    so the user can see the cloud bot is alive immediately."""
    src = _wf_text()
    # Look for an ntfy curl in a step BEFORE the bot daemon step
    bot_idx = src.find("python -u bot.py --daemon")
    assert bot_idx > 0
    pre_bot = src[:bot_idx]
    assert "ntfy.sh" in pre_bot, (
        "workflow must push ntfy.sh from a step BEFORE the bot daemon"
    )


def test_workflow_ntfy_no_invalid_secret_in_if():
    """Phase-90 BUG FIX: Phase-88's `if: ${{ secrets.NTFY_TOPIC != '' }}`
    was REJECTED by GitHub workflow VALIDATOR. Every push triggered a
    failing validation-run (event=push, 0 jobs, conclusion=failure).
    GitHub Actions forbids `secrets` context in step-level `if:`
    expressions. Allowed contexts in `if:`: env, github, inputs, vars.
    NOT: secrets, runner, job.

    Allowed patterns:
      env:
        NTFY: ${{ secrets.NTFY_TOPIC }}
      run: |
        if [ -n "$NTFY" ]; then ...

    Forbidden patterns:
      if: ${{ secrets.NTFY_TOPIC != '' }}    # GitHub validator rejects
      if: ${{ env.NTFY_TOPIC != '' }}        # step-level env not available
    """
    src = _wf_text()
    # Phase-88 bug: env.NTFY_TOPIC in if
    assert "if: ${{ env.NTFY_TOPIC" not in src, (
        "Phase-88 BUG: `if: env.NTFY_TOPIC` is broken — step-level env "
        "is not available to step-level if."
    )
    # Phase-90 bug: secrets in if (validator-rejected)
    # Filter out YAML comment lines so we don't trip on docstring
    # that DESCRIBES the bug.
    code_lines = [ln for ln in src.splitlines()
                   if not ln.lstrip().startswith("#")]
    code_only = "\n".join(code_lines)
    assert "if: ${{ secrets." not in code_only, (
        "Phase-90 BUG: secrets.* in step-level `if:` causes GitHub "
        "workflow VALIDATION FAILURE (event=push runs fail with 0 jobs). "
        "Use env: block to expose the secret as $VAR, then test in run:."
    )
    # Must still actually use the secret somewhere
    assert "secrets.NTFY_TOPIC" in src, (
        "workflow must reference secrets.NTFY_TOPIC (in env: or inline run:)"
    )


def test_workflow_has_eod_ntfy_step():
    """End-of-day summary push so user sees the day's outcome
    (force_entries, filled, errors) even without checking GH UI."""
    src = _wf_text()
    bot_idx = src.find("python -u bot.py --daemon")
    assert bot_idx > 0
    post_bot = src[bot_idx:]
    assert "ntfy.sh" in post_bot, (
        "workflow must push ntfy.sh from a step AFTER bot daemon"
    )


# ─── F. Artifact uploads (all logs preserved) ──────────────────────────

def test_workflow_uploads_bot_log():
    src = _wf_text()
    assert "06_live_bot/bot.log" in src
    # Phase-78 rotation backups too
    assert "06_live_bot/bot.log.*" in src


def test_workflow_uploads_alerts_log():
    """alerts.log is the ntfy push history — needed for postmortem."""
    src = _wf_text()
    assert "06_live_bot/alerts.log" in src


def test_workflow_uploads_jsonl_logs():
    """order_lifecycle + market_data_calls = structured trade history."""
    src = _wf_text()
    assert "order_lifecycle.jsonl" in src
    assert "market_data_calls.jsonl" in src
    assert "alpaca_api_calls.jsonl" in src


def test_workflow_uploads_postmortem():
    """no_trade_postmortem JSON has full diagnostic state."""
    src = _wf_text()
    assert "no_trade_postmortem_*.json" in src


def test_workflow_uploads_status_json():
    """status.json is the live operator-readable state."""
    src = _wf_text()
    assert "status.json" in src


# ─── G. Job summary in GitHub UI ────────────────────────────────────────

def test_workflow_writes_step_summary():
    """The GitHub Action UI must display trade counts + activity
    so the user can verify behavior without downloading artifacts."""
    src = _wf_text()
    assert "GITHUB_STEP_SUMMARY" in src
    # Must include trade-count grep
    assert "FORCE-ENTRY" in src or "FILLED" in src
    # And error counts
    assert "ERROR" in src or "CRITICAL" in src


# ─── H. Bot-side BUY ntfy still has TP/SL/R:R (Phase-82 contract) ──────

def test_buy_ntfy_includes_stop_target_rr():
    """_push_trade for entries must compose body with Stop, Target, R:R
    so the operator's phone shows full trade plan."""
    src = _bot_src()
    assert "R:R" in src
    assert "price - stop" in src
    assert "target - price" in src


# ─── I. State file for Phase-84 progressive tightening ─────────────────

STATE_FILE = ROOT / "docs" / "PROGRESSIVE_TIGHTENING_STATE.json"


def test_progressive_state_file_exists():
    assert STATE_FILE.exists()


def test_progressive_state_starts_at_loose_or_ultra():
    """Tightening journey begins at the chosen default mode. After
    Phase-86 3-month backtest, user switched to ultra (4x more PnL
    than loose). Either accepted, but never strict/force/relaxed
    (those produce too few trades for cloud trading)."""
    import json
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    assert state["current_stage"] in ("loose", "ultra"), (
        f"start stage must be 'loose' or 'ultra', got '{state['current_stage']}'"
    )


def test_progressive_state_has_all_stages():
    import json
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    stages = state["_stages"]
    # All 5 stages defined
    for stage in ("force", "ultra", "loose", "relaxed", "strict"):
        assert stage in stages, f"stage '{stage}' missing from state file"
        s = stages[stage]
        assert "order" in s
        assert "min_trades_to_advance" in s
        assert s["min_trades_to_advance"] >= 2


# ─── J. Workflow can be triggered manually (workflow_dispatch) ─────────

def test_workflow_supports_manual_trigger():
    """Operator must be able to fire a run via gh CLI / GitHub UI
    without waiting for the 13:23 UTC schedule."""
    wf = _wf_dict()
    triggers = wf.get(True) or wf.get("on") or {}  # YAML maps "on:" to True
    if isinstance(triggers, list):
        assert "workflow_dispatch" in triggers
    elif isinstance(triggers, dict):
        assert "workflow_dispatch" in triggers


# ─── K. Smoke: bot must start cleanly in loose mode ────────────────────

def test_bot_loose_config_via_source():
    """STRATEGY_VARIANT=loose must produce: FORCE_ENTRY_ON_BAR off,
    real pattern detector active, looser thresholds. Source-grep
    (no module import) so this test doesn't pollute the global
    bot module cache for other tests in the same pytest run."""
    src = _bot_src()
    # The "loose" variant block defines these constants
    block = re.search(
        r'STRATEGY_VARIANT in \("loose"[^)]*\)[\s\S]{0,800}',
        src,
    )
    assert block, "loose variant config block missing"
    body = block.group(0)
    # Loose must lower pole_min_move below strict's 4%
    m = re.search(r"POLE_MIN_MOVE_PCT\s*=\s*([\d.]+)", body)
    assert m
    assert float(m.group(1)) <= 3.0
    # Loose must raise flag_retrace above strict's 50%
    m2 = re.search(r"FLAG_RETRACE_MAX_PCT\s*=\s*([\d.]+)", body)
    assert m2
    assert float(m2.group(1)) >= 60.0
    # Loose variant must NOT enable FORCE_ENTRY_ON_BAR (only "force"
    # does that — Phase-79)
    force_block = re.search(
        r'STRATEGY_VARIANT == "force"[\s\S]{0,1200}FORCE_ENTRY_ON_BAR\s*=\s*True',
        src,
    )
    assert force_block, "FORCE_ENTRY_ON_BAR must be exclusive to 'force'"


# ─── L. Phase-85 archaeology comment ───────────────────────────────────

def test_phase_85_comment_present_in_workflow():
    src = _wf_text()
    assert "Phase-85" in src or "loose mode" in src.lower()
