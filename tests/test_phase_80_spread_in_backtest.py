"""Phase-80 (2026-05-19): realistic bid/ask spread in backtest.

User: "auch für den test immer immer auch spreads annehmen, auch im
backtest"

The v2 backtest used a flat 1¢ slippage on entry + stop fills. For
penny-stocks at $2-3, that's 0.3-0.5% slippage — way too tight.
Real bid/ask spread on Alpaca paper for small-caps in RTH is 30-80
bps (0.30-0.80%) — sometimes wider in pre/post-market. Today's force-
mode live test confirmed this: a 1% stop on $2 CODX instant-stopped on
the bid/ask spread.

Phase-80 replaces flat-cent slippage with a percent-based bid/ask
spread model:

  SPREAD_BPS = 50           # 0.50% total spread
  HALF_SPREAD = 0.0025      # paid as half on entry + half on exit

  entry_with_spread(p) = p * (1 + half_spread) + 1¢
  exit_with_spread(p)  = p * (1 - half_spread) - 1¢

Applied at every fill point:
  - Entry (limit-buy)
  - Stop-hit fill
  - T1 + T2 take-profit fills
  - MACD-cross-down market-exit
  - EOD market-exit

Expected backtest impact: PnL drops 10-30% — more conservative,
matches the live-execution reality.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "04_backtest"))


def _src() -> str:
    return (ROOT / "04_backtest" / "backtest_bull_flag_v2.py").read_text(
        encoding="utf-8"
    )


def test_spread_constants_defined():
    """SPREAD_BPS + HALF_SPREAD constants must exist in v2."""
    src = _src()
    assert "SPREAD_BPS" in src
    assert "HALF_SPREAD" in src


def test_spread_value_realistic():
    """50 bps total / 25 bps half is the conservative-realistic level
    for $2-20 small-caps on Alpaca paper. Wider = too pessimistic,
    tighter = back to the 1¢ unrealism."""
    src = _src()
    import re
    m = re.search(r"SPREAD_BPS\s*=\s*(\d+)", src)
    assert m, "SPREAD_BPS must be a numeric literal in v2"
    val = int(m.group(1))
    assert 30 <= val <= 150, f"SPREAD_BPS {val} out of realistic range 30-150"


def test_entry_with_spread_function_exists():
    """entry_with_spread() helper must be a top-level def in v2."""
    src = _src()
    assert "def entry_with_spread(" in src
    assert "def exit_with_spread(" in src


def test_entry_with_spread_logic_adds_half_spread():
    """The entry function must MULTIPLY planned by (1 + HALF_SPREAD)
    so the cost scales with price (1% of $5 vs 1% of $20 must differ).
    Old flat-cent approach was constant in $ which is wrong."""
    src = _src()
    import re
    block = re.search(
        r"def entry_with_spread[\s\S]{0,300}?return[\s\S]{0,100}"
        r"\*\s*\(\s*1\.0?\s*\+\s*HALF_SPREAD\s*\)",
        src,
    )
    assert block, "entry_with_spread must multiply by (1 + HALF_SPREAD)"


def test_exit_with_spread_logic_subtracts_half_spread():
    src = _src()
    import re
    block = re.search(
        r"def exit_with_spread[\s\S]{0,300}?return[\s\S]{0,100}"
        r"\*\s*\(\s*1\.0?\s*-\s*HALF_SPREAD\s*\)",
        src,
    )
    assert block, "exit_with_spread must multiply by (1 - HALF_SPREAD)"


def test_entry_call_site_uses_spread_function():
    """The detect_bull_flag entry-price calc must call entry_with_spread,
    not the old SLIPPAGE_ENTRY_CENTS-only formula."""
    src = _src()
    # The body around the entry-price assignment must use entry_with_spread
    import re
    block = re.search(
        r"entry_price\s*=\s*entry_with_spread\(",
        src,
    )
    assert block, "detect_bull_flag must use entry_with_spread() for entry"


def test_stop_fill_uses_spread_function():
    """Stop-hit exit must hit the bid via exit_with_spread."""
    src = _src()
    import re
    block = re.search(
        r"exit_price\s*=\s*exit_with_spread\(\s*stop",
        src,
    )
    assert block, "Stop-hit must use exit_with_spread(stop)"


def test_target_fills_use_spread_function():
    """T1+T2 take-profit fills must hit the bid."""
    src = _src()
    assert "exit_with_spread(trade.target1_price)" in src
    assert "exit_with_spread(trade.target2_price)" in src


def test_eod_exit_uses_spread_function():
    """EOD-exit also hits the bid."""
    src = _src()
    assert "exit_with_spread(float(after" in src or \
           "exit_with_spread(float(after[" in src


def test_phase_80_comment_present():
    src = _src()
    assert "Phase-80" in src
    assert "spread" in src.lower()


def test_legacy_slippage_constants_still_exist():
    """Don't break backward-compat with code that imports
    SLIPPAGE_ENTRY_CENTS / SLIPPAGE_STOP_CENTS."""
    src = _src()
    assert "SLIPPAGE_ENTRY_CENTS" in src
    assert "SLIPPAGE_STOP_CENTS" in src
