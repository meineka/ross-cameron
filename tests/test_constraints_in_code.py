"""Cross-Reference: Code-Konstanten müssen mit constraints.yaml übereinstimmen."""
import yaml
from pathlib import Path
import bot

YAML_PATH = Path(__file__).resolve().parent.parent / "03_rules_engine" / "constraints.yaml"


def load_yaml():
    return yaml.safe_load(open(YAML_PATH, encoding="utf-8"))


def test_price_range_matches_yaml():
    y = load_yaml()
    assert bot.PRICE_MIN == y["universe"]["price_min_usd"]
    assert bot.PRICE_MAX == y["universe"]["price_max_usd"]


def test_pole_min_pct_matches_yaml():
    y = load_yaml()
    yaml_min = y["entries"]["bull_flag_micro_pullback"]["pole"]["cumulative_move_pct_min"]
    assert bot.POLE_MIN_MOVE_PCT == yaml_min


def test_breakout_vol_factor_matches_yaml():
    y = load_yaml()
    yaml_factor = y["entries"]["bull_flag_micro_pullback"]["breakout"]["volume_factor_min"]
    assert bot.BREAKOUT_VOL_FACTOR == yaml_factor


def test_topping_tail_max_matches_yaml():
    y = load_yaml()
    yaml_topping = y["entries"]["bull_flag_micro_pullback"]["pole"]["no_topping_tail"]["upper_wick_to_range_ratio_max"]
    assert bot.POLE_TOPPING_TAIL_MAX == yaml_topping


def test_flag_retrace_matches_yaml():
    y = load_yaml()
    yaml_retrace = y["entries"]["bull_flag_micro_pullback"]["flag"]["retracement_pct_max"]
    assert bot.FLAG_RETRACE_MAX_PCT == yaml_retrace
