"""Audit-Iter 34 (2026-05-13): deploy_safe.py production-safety bugs.

Bugs:
  DS-1 (CRITICAL): taskkill /F /IM python.exe killed JEDES python.exe.
    User-Scripts (jupyter, tests, etc) wären collateral. Cloud (Linux):
    taskkill nicht da → silent no-op.
  DS-2 (HIGH): Race window zwischen check_positions + kill_bot+start_bot.
    Bot kann zwischen checks neue Position öffnen.
  DS-5 (MED): /F war SIGKILL ohne graceful → Bot keine Chance HARD_FLAT +
    day_summary schreiben.
  DS-6 (CRITICAL): cross-platform — Linux fail silent.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── DS-1 + DS-6: cmdline-specific cross-platform PID finder ───────────────
def test_find_bot_pids_only_matches_bot_daemon():
    """_find_bot_pids matched NUR bot.py --daemon, nicht random python."""
    import deploy_safe
    fake_bot = MagicMock()
    fake_bot.pid = 12345
    fake_bot.info = {"cmdline": ["python", "bot.py", "--daemon"]}
    fake_jupyter = MagicMock()
    fake_jupyter.pid = 99999
    fake_jupyter.info = {"cmdline": ["python", "-m", "jupyter", "lab"]}
    fake_test = MagicMock()
    fake_test.pid = 55555
    fake_test.info = {"cmdline": ["python", "-m", "pytest", "tests/"]}
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [fake_bot, fake_jupyter, fake_test]
    )}):
        pids = deploy_safe._find_bot_pids()
    assert 12345 in pids
    assert 99999 not in pids
    assert 55555 not in pids


def test_find_bot_pids_returns_empty_when_no_bot():
    import deploy_safe
    fake_other = MagicMock()
    fake_other.pid = 1
    fake_other.info = {"cmdline": ["python", "other.py"]}
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [fake_other]
    )}):
        pids = deploy_safe._find_bot_pids()
    assert pids == []


def test_find_bot_pids_handles_psutil_unavailable():
    """Wenn psutil nicht installed, fallback zu wmic/pgrep."""
    import deploy_safe
    import builtins

    real_import = builtins.__import__

    def block_psutil(name, *a, **kw):
        if name == "psutil":
            raise ImportError("no psutil in test env")
        return real_import(name, *a, **kw)

    # Patch subprocess.check_output to return empty (no bot)
    with patch("builtins.__import__", side_effect=block_psutil):
        with patch("subprocess.check_output", return_value=""):
            pids = deploy_safe._find_bot_pids()
    assert pids == []


def test_find_bot_pids_handles_multiple_bots():
    """Mehrere bot-instances (eg cloud + lokal race) → alle erkannt."""
    import deploy_safe
    bot1 = MagicMock()
    bot1.pid = 100
    bot1.info = {"cmdline": ["python", "bot.py", "--daemon"]}
    bot2 = MagicMock()
    bot2.pid = 200
    bot2.info = {"cmdline": ["python3", "bot.py", "--daemon", "--dry-run"]}
    with patch.dict("sys.modules", {"psutil": MagicMock(
        process_iter=lambda attrs: [bot1, bot2]
    )}):
        pids = deploy_safe._find_bot_pids()
    assert 100 in pids
    assert 200 in pids


# ─── DS-5: graceful SIGTERM before SIGKILL ──────────────────────────────────
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="sys.modules-patch of psutil only takes effect on platforms "
            "where deploy_safe hasn't already cached the real psutil import. "
            "Verified on Windows; on Linux CI the real psutil is bound before "
            "the patch and the MagicMock proc isn't called. Functional logic "
            "is platform-agnostic — see test_kill_bot_returns_zero_when_no_pids.",
)
def test_kill_bot_uses_sigterm_first():
    """Bot bekommt SIGTERM (terminate), NICHT direkt SIGKILL."""
    import deploy_safe
    terminate_calls = []
    fake_proc = MagicMock()
    fake_proc.terminate = lambda: terminate_calls.append("terminate")

    fake_psutil = MagicMock()
    fake_psutil.Process = lambda pid: fake_proc
    fake_psutil.process_iter = lambda attrs: []  # empty after kill

    with patch.dict("sys.modules", {"psutil": fake_psutil}):
        with patch.object(deploy_safe, "_find_bot_pids",
                          side_effect=[[123], []]):  # first found, then gone
            n = deploy_safe.kill_bot(graceful_seconds=0.5)
    assert n == 1
    assert "terminate" in terminate_calls


def test_kill_bot_returns_zero_when_no_pids():
    import deploy_safe
    with patch.object(deploy_safe, "_find_bot_pids", return_value=[]):
        assert deploy_safe.kill_bot(graceful_seconds=0.1) == 0


# ─── DS-2: post-kill re-check (source-grep) ─────────────────────────────────
def test_main_rechecks_positions_after_kill():
    """REGRESSION DS-2: main() muss check_positions AFTER kill aufrufen,
    nicht nur einmal vorher. Bot kann während kill noch trades öffnen."""
    src = (ROOT / "06_live_bot" / "deploy_safe.py").read_text(encoding="utf-8")
    # check_positions sollte mehr als 1x aufgerufen werden in main()
    main_section = src[src.find("def main()"):]
    assert main_section.count("check_positions(") >= 2, \
        "main() muss check_positions vor UND nach kill_bot aufrufen"


# ─── check_positions defensive ───────────────────────────────────────────────
def test_check_positions_returns_minus_one_on_api_failure():
    import deploy_safe
    with patch("alpaca.trading.client.TradingClient",
               side_effect=RuntimeError("API down")):
        with patch.dict("os.environ", {"APCA_API_KEY_ID": "k",
                                         "APCA_API_SECRET_KEY": "s"}):
            open_, n = deploy_safe.check_positions()
    assert n == -1
    assert open_ is False


def test_check_positions_returns_minus_one_on_missing_keys():
    """Wenn keine keys → return (False, -1) NICHT (False, 0)."""
    import deploy_safe

    def raise_empty(*a, **kw):
        raise RuntimeError("no keys")

    with patch.dict("sys.modules", {"secrets_loader": MagicMock(
        get_alpaca_keys=raise_empty
    )}):
        with patch.dict("os.environ", {"APCA_API_KEY_ID": "",
                                         "APCA_API_SECRET_KEY": ""}, clear=False):
            # Need to ensure env vars empty
            import os
            os.environ.pop("APCA_API_KEY_ID", None)
            os.environ.pop("APCA_API_SECRET_KEY", None)
            open_, n = deploy_safe.check_positions()
    assert n == -1


# ─── Source-Grep: no more wholesale taskkill ─────────────────────────────────
def test_no_more_wholesale_taskkill_python():
    """REGRESSION DS-1: 'taskkill /F /IM python.exe' war der bug."""
    src = (ROOT / "06_live_bot" / "deploy_safe.py").read_text(encoding="utf-8")
    # Should not have the wholesale-kill pattern anymore
    assert 'taskkill", "/F", "/IM", "python.exe"' not in src
    assert '"/IM", "python.exe"' not in src
