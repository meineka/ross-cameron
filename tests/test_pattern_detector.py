"""Bull-Flag Pattern-Detector Tests — synthetic bars."""
import numpy as np
from bot import detect_bull_flag, PRICE_MIN, PRICE_MAX


def make_bar(o, h, l, c, v):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "timestamp": None}


def warmup(n_bars=15, base_price=5.0, vol=10000):
    """Sideways warmup-bars for vol_sma initialization."""
    bars = []
    for _ in range(n_bars):
        bars.append(make_bar(base_price, base_price + 0.02, base_price - 0.02, base_price + 0.01, vol))
    return bars


def test_clean_bull_flag_detected():
    """3 grüne Pole-Kerzen 6%, 1 rote Flag-Kerze, Breakout."""
    bars = warmup()
    p = 5.0
    # Pole: 3 green candles, +6% total
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p = 5.10
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p = 5.20
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p = 5.30   # pole_end
    # Flag: 1 red candle, retrace ~25%
    bars.append(make_bar(p, p+0.01, p-0.07, p-0.05, 8000)); p = 5.25
    # Breakout: green candle, high > prev red high (5.31)
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.08, 30000))
    sig, params = detect_bull_flag(bars)
    assert sig is True
    assert params["entry_price"] > p
    assert params["stop_price"] < params["entry_price"]


def test_pole_too_weak_rejected():
    """Pole nur 2% → muss skip."""
    bars = warmup()
    p = 5.0
    for _ in range(3):
        bars.append(make_bar(p, p+0.04, p-0.01, p+0.03, 30000)); p += 0.03
    bars.append(make_bar(p, p+0.01, p-0.04, p-0.02, 8000)); p -= 0.02
    bars.append(make_bar(p, p+0.10, p, p+0.06, 30000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False


def test_red_candle_breakout_rejected():
    bars = warmup()
    p = 5.0
    for _ in range(3):
        bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p += 0.10
    bars.append(make_bar(p, p+0.01, p-0.07, p-0.05, 8000)); p -= 0.05
    # Breakout candle is RED
    bars.append(make_bar(p, p+0.05, p-0.10, p-0.05, 30000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False


def test_low_volume_breakout_rejected():
    bars = warmup(vol=10000)
    p = 5.0
    for _ in range(3):
        bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p += 0.10
    bars.append(make_bar(p, p+0.01, p-0.07, p-0.05, 8000)); p -= 0.05
    # Volume below 1.5x SMA
    bars.append(make_bar(p, p+0.10, p, p+0.08, 5000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False


def test_topping_tail_on_pole_rejected():
    """Pole-Kerze hat Topping-Tail > 40% Range → skip."""
    bars = warmup()
    p = 5.0
    bars.append(make_bar(p, p+0.20, p-0.01, p+0.05, 30000)); p = 5.05  # big upper wick
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p = 5.15
    bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p = 5.25
    bars.append(make_bar(p, p+0.01, p-0.05, p-0.04, 8000)); p = 5.21
    bars.append(make_bar(p, p+0.10, p, p+0.06, 30000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False, "Topping-Tail-Pole soll skip"


def test_flag_retrace_too_deep_rejected():
    """Flag retraced > 50% → skip."""
    bars = warmup()
    p = 5.0
    for _ in range(3):
        bars.append(make_bar(p, p+0.10, p-0.01, p+0.10, 30000)); p += 0.10
    # Retrace > 50% von pole_height (0.30)
    bars.append(make_bar(p, p+0.01, p-0.20, p-0.20, 8000)); p -= 0.20
    bars.append(make_bar(p, p+0.10, p, p+0.05, 30000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False


def test_price_below_min_rejected():
    """Breakout-Bar Close < $2 → skip (Bug-Fix-Regression-Test)."""
    bars = warmup(base_price=0.5, vol=10000)
    p = 0.5
    for _ in range(3):
        bars.append(make_bar(p, p+0.05, p-0.01, p+0.05, 30000)); p += 0.05
    bars.append(make_bar(p, p+0.01, p-0.03, p-0.02, 8000)); p -= 0.02
    bars.append(make_bar(p, p+0.05, p, p+0.03, 30000))   # close ~$0.66
    sig, _ = detect_bull_flag(bars)
    assert sig is False, "Preis unter $2 muss skip"


def test_price_above_max_rejected():
    """Breakout-Bar Close > $20 → skip."""
    bars = warmup(base_price=25.0, vol=10000)
    p = 25.0
    for _ in range(3):
        bars.append(make_bar(p, p+0.50, p-0.01, p+0.50, 30000)); p += 0.50
    bars.append(make_bar(p, p+0.01, p-0.30, p-0.25, 8000)); p -= 0.25
    bars.append(make_bar(p, p+0.50, p, p+0.30, 30000))
    sig, _ = detect_bull_flag(bars)
    assert sig is False, "Preis ueber $20 muss skip"


def test_too_few_bars_rejected():
    """Weniger Bars als Min → skip."""
    bars = warmup(n_bars=2)
    sig, _ = detect_bull_flag(bars)
    assert sig is False
