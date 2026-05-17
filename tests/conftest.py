"""conftest.py — pytest setup für Cameron-Bot Tests."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "06_live_bot"))
sys.path.insert(0, str(ROOT / "04_backtest"))


# Phase-66.1 (2026-05-17): the real 06_live_bot/.env now persists
# STRATEGY_VARIANT=relaxed for the live bot. Tests that just do
# `import bot` would inherit that and break dozens of strict-assumption
# tests (test_risk_engine, test_position_size_multipliers, …).
#
# Fix: at test-session start, force strict-mode env var BEFORE any
# `import bot` runs. Individual tests that need to test variant
# behaviour (test_phase_66_*) still monkeypatch+reimport explicitly.
os.environ["STRATEGY_VARIANT"] = "strict"
