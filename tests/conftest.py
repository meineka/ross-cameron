"""conftest.py — pytest setup für Cameron-Bot Tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "06_live_bot"))
sys.path.insert(0, str(ROOT / "04_backtest"))
