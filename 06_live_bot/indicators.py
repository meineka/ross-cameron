"""Cameron-Indikatoren: MACD 12/26/9, RSI(14), False-Breakout-Filter.

Implementiert die Vetos die bisher nur als Counter geloggt wurden:
- MACD bullish-cross (Entry) + bearish-cross (Exit)
- FBO 5-Indicator (False-Breakout-Filter)
"""
from __future__ import annotations
from typing import Sequence
import numpy as np
import pandas as pd


# ─── MACD ────────────────────────────────────────────────────────────────────
def macd(closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, hist) — numpy arrays."""
    c = pd.Series(closes)
    ema_fast = c.ewm(span=fast, adjust=False).mean()
    ema_slow = c.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line
    return macd_line.to_numpy(), sig_line.to_numpy(), hist.to_numpy()


def macd_is_bullish(closes: Sequence[float]) -> bool:
    """MACD-Line > Signal UND MACD-Line > 0 → Aufwärts-Momentum.
    Cameron: 'Don't fight the MACD'."""
    if len(closes) < 30:
        return True  # zu wenig Daten → kein Veto
    m, s, _ = macd(closes)
    return float(m[-1]) > float(s[-1]) and float(m[-1]) > 0


def macd_bear_cross(closes: Sequence[float]) -> bool:
    """True wenn beim LETZTEN Bar Bullish→Bearish-Cross stattfand → Exit-Signal."""
    if len(closes) < 30:
        return False
    m, s, _ = macd(closes)
    return float(m[-2]) > float(s[-2]) and float(m[-1]) <= float(s[-1])


# ─── RSI ─────────────────────────────────────────────────────────────────────
def rsi(closes: Sequence[float], period: int = 14) -> float:
    c = pd.Series(closes)
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0.0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    val = out.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


# ─── False-Breakout-Filter (FBO 5-Indicator) ─────────────────────────────────
def false_breakout_veto(bars: list[dict]) -> tuple[bool, str]:
    """Returns (vetoed, reason). True = REJECT (looks like false breakout).

    Cameron's 5 Indikatoren:
      1) Topping-Tail > 50 % der Range auf Breakout-Bar
      2) Volume < 1.5× SMA20 (separate check exists, doppeln zur Sicherheit)
      3) Close in unterstem Drittel der Bar-Range
      4) RSI > 80 (overbought, Mean-Reversion-Risk)
      5) Keine 2 grünen Bestätigungs-Bars vor Breakout
    """
    if len(bars) < 22:
        return False, ""
    b = bars[-1]
    rng = b["high"] - b["low"]
    if rng <= 0:
        return False, ""
    # 1) Topping-Tail
    upper_wick = b["high"] - max(b["close"], b["open"])
    if upper_wick / rng > 0.5:
        return True, "topping_tail>50%"
    # 3) Close im unteren Drittel
    if (b["close"] - b["low"]) / rng < 0.33:
        return True, "close_in_lower_third"
    # 4) RSI overbought
    closes = [x["close"] for x in bars]
    r = rsi(closes, 14)
    if r > 80:
        return True, f"rsi_overbought_{r:.0f}"
    # 5) 2 grüne Bestätigungs-Bars davor
    prev2 = bars[-3:-1]
    if len(prev2) == 2:
        greens = sum(1 for x in prev2 if x["close"] > x["open"])
        if greens < 1:  # mindestens 1 grün davor
            return True, "no_green_confirm_bars"
    return False, ""
