"""Constraints-YAML Struktur-Tests."""
import yaml
from pathlib import Path

YAML = Path(__file__).resolve().parent.parent / "03_rules_engine" / "constraints.yaml"


def load():
    return yaml.safe_load(open(YAML, encoding="utf-8"))


def test_yaml_parses():
    d = load()
    assert isinstance(d, dict)
    assert len(d) >= 15


def test_canonical_sections_present():
    d = load()
    required = ["universe", "session", "indicators", "entries",
                "halt_mechanics", "pullback_count_rule", "risk", "vetos",
                "false_breakout_filter", "extended_hours_mechanics"]
    for k in required:
        assert k in d, f"missing top-level: {k}"


def test_universe_5_pillars():
    d = load()["universe"]
    assert d["price_min_usd"] == 2.0
    assert d["price_max_usd"] == 20.0
    assert d["rvol_min"] == 5.0
    assert d["daily_change_min_pct"] == 10.0
    assert d["catalyst_required"] is True


def test_halt_mechanics_canonical():
    d = load()["halt_mechanics"]
    assert d["level_1_trigger_pct_in_5min"] == 10
    assert d["level_2_trigger_pct_above_5min_avg"] == 20
    assert d["bid_hold_seconds_required"] == 15


def test_pullback_count_rule():
    d = load()["pullback_count_rule"]
    assert d["pullback_1"] == "aggressive_entry_ok"
    assert d["pullback_2"] == "aggressive_entry_ok"
    assert d["pullback_4_plus"] == "skip_always"


def test_indicators_macd_locked():
    d = load()["indicators"]["macd"]
    assert d["fast"] == 12 and d["slow"] == 26 and d["signal"] == 9
    assert d["locked"] is True


def test_vetos_count():
    d = load()["vetos"]
    assert isinstance(d, list)
    assert len(d) >= 25
