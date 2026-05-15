"""Cross-Reference: Code-Konstanten müssen mit constraints.yaml konsistent sein.

Phase-33 (2026-05-15): the YAML is Cameron-strict; the bot may be running
USER-OVERRIDE looser values to admit more candidates / setups. Tests
verify "bot value is AT LEAST as lax as YAML" (looser-or-equal) rather
than strict equality, but still bound the deviation so a typo can't
disable a filter entirely.
"""
import yaml
from pathlib import Path
import bot

YAML_PATH = Path(__file__).resolve().parent.parent / "03_rules_engine" / "constraints.yaml"


def load_yaml():
    return yaml.safe_load(open(YAML_PATH, encoding="utf-8"))


def test_price_range_within_yaml_envelope():
    """Bot PRICE_MIN may be <= YAML strict, PRICE_MAX may be >= YAML strict
    (user-override widens the price band). But both must stay in a
    sane range so the strategy is still 'small-mid-cap movers'."""
    y = load_yaml()
    assert bot.PRICE_MIN <= y["universe"]["price_min_usd"]
    assert bot.PRICE_MIN >= 1.0  # never below $1 — penny-stock zone
    assert bot.PRICE_MAX >= y["universe"]["price_max_usd"]
    assert bot.PRICE_MAX <= 50.0  # never above $50 — out of Cameron's spec


def test_pole_min_pct_at_most_yaml():
    """Bot may be looser (lower threshold) than YAML."""
    y = load_yaml()
    yaml_min = y["entries"]["bull_flag_micro_pullback"]["pole"]["cumulative_move_pct_min"]
    assert bot.POLE_MIN_MOVE_PCT <= yaml_min, \
        f"POLE_MIN_MOVE_PCT={bot.POLE_MIN_MOVE_PCT}% must be <= yaml-strict {yaml_min}%"
    assert bot.POLE_MIN_MOVE_PCT >= 1.0, \
        f"POLE_MIN_MOVE_PCT={bot.POLE_MIN_MOVE_PCT}% too lax (<1% = noise)"


def test_breakout_vol_factor_at_most_yaml():
    y = load_yaml()
    yaml_factor = y["entries"]["bull_flag_micro_pullback"]["breakout"]["volume_factor_min"]
    assert bot.BREAKOUT_VOL_FACTOR <= yaml_factor
    assert bot.BREAKOUT_VOL_FACTOR >= 1.0  # always > average volume


def test_topping_tail_max_at_least_yaml():
    """Higher topping-tail tolerance = looser. YAML is strict, bot may
    be more permissive."""
    y = load_yaml()
    yaml_topping = y["entries"]["bull_flag_micro_pullback"]["pole"]["no_topping_tail"]["upper_wick_to_range_ratio_max"]
    assert bot.POLE_TOPPING_TAIL_MAX >= yaml_topping
    assert bot.POLE_TOPPING_TAIL_MAX <= 0.8  # never accept >80% wick


def test_flag_retrace_at_least_yaml():
    """Higher retrace ceiling = looser flag."""
    y = load_yaml()
    yaml_retrace = y["entries"]["bull_flag_micro_pullback"]["flag"]["retracement_pct_max"]
    assert bot.FLAG_RETRACE_MAX_PCT >= yaml_retrace
    assert bot.FLAG_RETRACE_MAX_PCT <= 80.0  # not a full retrace
