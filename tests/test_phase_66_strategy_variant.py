"""Phase-66: STRATEGY_VARIANT env var (strict vs relaxed).

User: "mach mal 2 strategien nach volumen, sprich konservativ,
       doppeltes volumen, mach ein kommentar mit strict-algo,
       ansonsten relaxed-algo"

Two-variant position sizing on the SAME Cameron-strict entry criteria:
  strict-algo  (default): MAX_LOSS=$50,  equity-cap=1%, DAILY_MAX=$150
  relaxed-algo (2× vol):  MAX_LOSS=$100, equity-cap=2%, DAILY_MAX=$300

These tests lock in:
  - env var routing to the right constants
  - 2× position-size at the compute_position_size level
  - safe default ("strict") when env var missing or invalid
  - per-trade-loss cap doubles, daily-loss cap doubles, equity-cap doubles
"""
from __future__ import annotations
import importlib
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture(autouse=True)
def _restore_strict_default_after_test():
    """Phase-66 tests reimport bot.py with different STRATEGY_VARIANT
    values. Without explicit teardown, the LAST-loaded variant leaks
    into subsequent tests in the suite that just do `import bot` and
    depend on the strict defaults.

    Phase-69 update: use os.environ direct (NOT monkeypatch), because
    monkeypatch.setenv reverts during its own teardown which can leave
    a stale 'loose'/'relaxed' value if a prior test's monkeypatch is
    being unwound in parallel.
    """
    import os
    yield
    os.environ["STRATEGY_VARIANT"] = "strict"
    if "bot" in sys.modules:
        del sys.modules["bot"]
    importlib.import_module("bot")


def _reimport_bot_with_variant(variant: str | None,
                                  monkeypatch) -> object:
    """Import bot.py with STRATEGY_VARIANT set to `variant`. Returns the
    freshly-reimported module so tests get the variant-conditional
    constants. monkeypatch ensures the env change is rolled back."""
    if variant is None:
        monkeypatch.delenv("STRATEGY_VARIANT", raising=False)
    else:
        monkeypatch.setenv("STRATEGY_VARIANT", variant)
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot  # noqa: F401
    return sys.modules["bot"]


# ─── 1. Constants per variant ────────────────────────────────────────────

def test_strict_variant_uses_50_dollar_per_trade(monkeypatch):
    bot = _reimport_bot_with_variant("strict", monkeypatch)
    assert bot.STRATEGY_VARIANT == "strict"
    assert bot.MAX_LOSS_PER_TRADE_USD == 50.0
    assert bot.DAILY_MAX_LOSS_USD == 150.0
    assert bot.EQUITY_RISK_CAP_PCT == 1.0


def test_relaxed_variant_uses_100_dollar_per_trade(monkeypatch):
    bot = _reimport_bot_with_variant("relaxed", monkeypatch)
    assert bot.STRATEGY_VARIANT == "relaxed"
    assert bot.MAX_LOSS_PER_TRADE_USD == 100.0
    assert bot.DAILY_MAX_LOSS_USD == 300.0
    assert bot.EQUITY_RISK_CAP_PCT == 2.0


def test_relaxed_daily_max_is_3x_per_trade(monkeypatch):
    """Spec: DAILY_MAX = 3× MAX_LOSS_PER_TRADE for both variants."""
    bot = _reimport_bot_with_variant("relaxed", monkeypatch)
    assert bot.DAILY_MAX_LOSS_USD == 3 * bot.MAX_LOSS_PER_TRADE_USD


def test_strict_daily_max_is_3x_per_trade(monkeypatch):
    bot = _reimport_bot_with_variant("strict", monkeypatch)
    assert bot.DAILY_MAX_LOSS_USD == 3 * bot.MAX_LOSS_PER_TRADE_USD


def test_relaxed_envelope_is_exactly_2x_strict(monkeypatch):
    """The 2× volume promise: every risk cap doubles."""
    strict = _reimport_bot_with_variant("strict", monkeypatch)
    s_max_loss = strict.MAX_LOSS_PER_TRADE_USD
    s_daily = strict.DAILY_MAX_LOSS_USD
    s_eq_pct = strict.EQUITY_RISK_CAP_PCT
    relaxed = _reimport_bot_with_variant("relaxed", monkeypatch)
    assert relaxed.MAX_LOSS_PER_TRADE_USD == 2 * s_max_loss
    assert relaxed.DAILY_MAX_LOSS_USD == 2 * s_daily
    assert relaxed.EQUITY_RISK_CAP_PCT == 2 * s_eq_pct


# ─── 2. Default + invalid handling ───────────────────────────────────────

def test_missing_env_var_defaults_to_strict(monkeypatch, tmp_path):
    """No shell env-var AND no .env entry → strict default.
    Phase-66.1 update: must also block .env (which now lives in the real
    repo with STRATEGY_VARIANT=relaxed) by pointing ENV_FILE at a
    deliberately-empty tmp file."""
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE", tmp_path / "absent.env")
    bot = _reimport_bot_with_variant(None, monkeypatch)
    assert bot.STRATEGY_VARIANT == "strict"
    assert bot.MAX_LOSS_PER_TRADE_USD == 50.0


def test_invalid_variant_falls_back_to_strict(monkeypatch):
    """Defensive: unknown variant string MUST NOT silently double risk —
    fall back to safe strict default."""
    bot = _reimport_bot_with_variant("aggressive", monkeypatch)
    assert bot.STRATEGY_VARIANT == "strict"
    assert bot.MAX_LOSS_PER_TRADE_USD == 50.0


def test_uppercase_variant_is_accepted_case_insensitively(monkeypatch):
    bot = _reimport_bot_with_variant("RELAXED", monkeypatch)
    assert bot.STRATEGY_VARIANT == "relaxed"
    assert bot.MAX_LOSS_PER_TRADE_USD == 100.0


# ─── 3. compute_position_size routes through variant ────────────────────

def test_compute_position_size_relaxed_is_2x_strict_with_same_inputs(
        monkeypatch):
    """Headline behavior: same entry/stop/equity, relaxed sizes 2× strict.

    Test inputs picked so that NEITHER the equity-cap NOR per-trade-loss
    cap is the binding constraint independently — the doubling propagates
    cleanly through. Risk-per-share = $0.50, equity = $100k:
      strict: max_shares = min(50/0.5=100, 1000/0.5=2000) = 100
      relaxed: max_shares = min(100/0.5=200, 2000/0.5=4000) = 200
    Both bound by MAX_LOSS cap; ratio is exactly 2.
    """
    from dataclasses import dataclass

    @dataclass
    class _DS:
        quarter_size_unlocked: bool = True

    from datetime import time
    ny = time(11, 0)  # post-power-hour, so POST_POWER_SIZE_MULT applies
    inputs = dict(entry=10.0, stop=9.5, account_equity=100_000.0,
                   day=_DS(), avg_volume=None, ny_time=ny)

    strict = _reimport_bot_with_variant("strict", monkeypatch)
    n_strict = strict.compute_position_size(**inputs)
    relaxed = _reimport_bot_with_variant("relaxed", monkeypatch)
    n_relaxed = relaxed.compute_position_size(**inputs)
    assert n_relaxed == 2 * n_strict, (
        f"relaxed should be 2× strict; got strict={n_strict} "
        f"relaxed={n_relaxed}"
    )


def test_compute_position_size_relaxed_capped_by_2pct_equity(monkeypatch):
    """When equity-cap is the binding constraint: relaxed uses 2% so
    relaxed shares = 2× strict shares (which uses 1%).

    Uses entry=$10/stop=$9.50 (clean $0.50 risk-per-share, no float-
    precision drift) and a small $2k account so the 1%/2% equity-cap
    is the binding constraint rather than the per-trade-loss cap.
      strict:  1% of $2k=$20, 20/0.5= 40 shares (equity-bound)
      relaxed: 2% of $2k=$40, 40/0.5= 80 shares (equity-bound)
      ratio 80/40 = 2 ✓
    """
    from dataclasses import dataclass

    @dataclass
    class _DS:
        quarter_size_unlocked: bool = True

    from datetime import time
    ny = time(11, 0)
    inputs = dict(entry=10.0, stop=9.5, account_equity=2_000.0,
                   day=_DS(), avg_volume=None, ny_time=ny)
    strict = _reimport_bot_with_variant("strict", monkeypatch)
    n_strict = strict.compute_position_size(**inputs)
    relaxed = _reimport_bot_with_variant("relaxed", monkeypatch)
    n_relaxed = relaxed.compute_position_size(**inputs)
    assert n_relaxed == 2 * n_strict, (
        f"strict={n_strict} relaxed={n_relaxed}"
    )
    # Sanity: equity-cap is actually binding (not the per-trade cap)
    # strict per-trade cap would be 50/0.5=100, equity-cap = 40 → 40 wins
    assert n_strict < 100, (
        f"test setup wrong: equity-cap should bind below "
        f"per-trade cap. strict={n_strict} (should be < 100)"
    )


def test_compute_position_size_relaxed_zero_when_invalid_inputs(
        monkeypatch):
    """Defensive: invalid inputs MUST still return 0 in relaxed —
    doubling 0 is 0, not some accidental risk."""
    from dataclasses import dataclass

    @dataclass
    class _DS:
        quarter_size_unlocked: bool = True

    relaxed = _reimport_bot_with_variant("relaxed", monkeypatch)
    assert relaxed.compute_position_size(
        entry=10.0, stop=10.5,  # stop above entry → invalid
        account_equity=10_000, day=_DS(),
    ) == 0
    assert relaxed.compute_position_size(
        entry=10.0, stop=9.5,
        account_equity=-1,  # broken account
        day=_DS(),
    ) == 0


# ─── 4. Code-marker annotations exist ───────────────────────────────────

def test_code_has_strict_algo_and_relaxed_algo_comments():
    """User-requested: code must visibly label which branch is which.
    Grep the source — comments must exist on the constants."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # At least one comment of each kind
    assert "strict-algo" in src
    assert "relaxed-algo" in src
    # AND on the doubled values specifically
    strict_lines = [L for L in src.splitlines() if "strict-algo" in L]
    relaxed_lines = [L for L in src.splitlines() if "relaxed-algo" in L]
    assert len(strict_lines) >= 3, (
        f"too few strict-algo annotations: {len(strict_lines)}"
    )
    assert len(relaxed_lines) >= 3, (
        f"too few relaxed-algo annotations: {len(relaxed_lines)}"
    )


def test_startup_log_mentions_variant():
    """Operator-visibility: daemon startup log must show which variant
    is active so a postmortem can't confuse the two."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "STRATEGY_VARIANT = %s" in src
    assert "MAX_LOSS_PER_TRADE_USD" in src
    assert "EQUITY_RISK_CAP_PCT" in src


# ─── 5. .env loading at module-load (Phase-66.1) ────────────────────────

def test_env_file_propagates_strategy_variant_to_module_load(
        tmp_path, monkeypatch):
    """Operator can pin STRATEGY_VARIANT in 06_live_bot/.env and bot.py
    will pick it up at module-load (NOT just from shell env-var).

    Without this wiring, the variant only worked via shell env-var and
    a Windows-reboot/watchdog-respawn would silently revert to strict.
    """
    import secrets_loader
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "APCA_API_KEY_ID=fake\n"
        "APCA_API_SECRET_KEY=fake\n"
        "STRATEGY_VARIANT=relaxed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_loader, "ENV_FILE", fake_env)
    monkeypatch.delenv("STRATEGY_VARIANT", raising=False)
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "relaxed"
    assert bot.MAX_LOSS_PER_TRADE_USD == 100.0


def test_shell_env_still_works_when_no_dotenv(monkeypatch):
    """Backward-compat: setting STRATEGY_VARIANT in shell env still
    takes effect."""
    monkeypatch.setenv("STRATEGY_VARIANT", "relaxed")
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "relaxed"


def test_shell_env_wins_over_dotenv_when_both_set(tmp_path, monkeypatch):
    """If both shell env-var and .env specify STRATEGY_VARIANT, shell
    wins (secrets_loader._load_env_file skips keys already in env)."""
    import secrets_loader
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "APCA_API_KEY_ID=fake\n"
        "APCA_API_SECRET_KEY=fake\n"
        "STRATEGY_VARIANT=strict\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_loader, "ENV_FILE", fake_env)
    monkeypatch.setenv("STRATEGY_VARIANT", "relaxed")
    if "bot" in sys.modules:
        del sys.modules["bot"]
    import bot
    assert bot.STRATEGY_VARIANT == "relaxed"
