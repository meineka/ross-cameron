"""conftest.py — pytest bootstrap for the REVIEW_PACKAGE layout.

This package has source under `src/` (not `06_live_bot/` as in the original
repo). We add both paths so any test that did `sys.path.insert(ROOT / "06_live_bot")`
still works (the path won't exist but `src` is already on path)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Primary: the review-package src layout
sys.path.insert(0, str(ROOT / "src"))
# Backward-compat: many tests do their own `sys.path.insert(0, ROOT / "06_live_bot")` —
# we already have src on path so module imports succeed.
