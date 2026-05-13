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
    """Audit-Iter 9 (2026-05-12) — Bug-Fix IND-6:
    Vorher: bei monotone uptrend (alle delta > 0) ist loss == 0 → durch
    replace(0, NaN) wurde rs = NaN → return 50. Aber bei reinem Aufwärtstrend
    sollte RSI = 100 sein. Folge: false_breakout_veto RSI>80-Regel feuerte
    NICHT bei parabolischen Chases — genau der Setup den sie filtern soll.
    """
    c = pd.Series(closes, dtype=float)
    delta = c.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0.0).rolling(period).mean()
    avg_gain = gain.iloc[-1]
    avg_loss = loss.iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return 50.0  # zu wenig Daten
    if avg_loss == 0:
        # Reiner Up- oder Flat-Trend
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100 - (100 / (1 + rs)))


# ─── False-Breakout-Filter (FBO 5-Indicator) ─────────────────────────────────
def false_breakout_veto(bars: list[dict]) -> tuple[bool, str]:
    """Returns (vetoed, reason). True = REJECT (looks like false breakout).

    Cameron's 5 Indikatoren:
      1) Topping-Tail > 50 % der Range auf Breakout-Bar
      2) Volume < 1.5× SMA20 (separate check exists, doppeln zur Sicherheit)
      3) Close in unterstem Drittel der Bar-Range
      4) RSI > 80 (overbought, Mean-Reversion-Risk)
      5) Keine grünen Bestätigungs-Bars vor Breakout

    Audit-Iter 9 (2026-05-12) — Bug-Fix IND-3:
    Vorher KeyError wenn bar key fehlt. Jetzt defensive: jeder Bar wird
    auf required-keys validiert, bar mit fehlenden Daten → return (False, "")
    statt crash (caller bekommt "alles ok" — etwas was UPSTREAM filter
    bereits aussortiert haben sollte, dieser Filter aber nicht auch
    wegen Daten-Glitch abstürzen darf).
    """
    if len(bars) < 22:
        return False, ""
    b = bars[-1]
    # Defensive key-check (IND-3)
    required = ("open", "high", "low", "close")
    if not all(k in b and b[k] is not None for k in required):
        return False, ""
    try:
        bo, bh, bl, bc = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
    except (TypeError, ValueError):
        return False, ""
    rng = bh - bl
    if rng <= 0:
        return False, ""
    # 1) Topping-Tail
    upper_wick = bh - max(bc, bo)
    if upper_wick / rng > 0.5:
        return True, "topping_tail>50%"
    # 3) Close im unteren Drittel
    if (bc - bl) / rng < 0.33:
        return True, "close_in_lower_third"
    # 4) RSI overbought
    try:
        closes = [float(x["close"]) for x in bars if "close" in x and x["close"] is not None]
    except (TypeError, ValueError):
        closes = []
    if len(closes) >= 22:
        r = rsi(closes, 14)
        if r > 80:
            return True, f"rsi_overbought_{r:.0f}"
    # 5) Mindestens 1 grünes Bestätigungs-Bar in den vorherigen 2
    prev2 = bars[-3:-1]
    if len(prev2) == 2:
        try:
            greens = sum(1 for x in prev2
                         if "close" in x and "open" in x and x["close"] > x["open"])
        except (TypeError, ValueError):
            greens = 2  # bei malformed bars nicht veto-en
        if greens < 1:
            return True, "no_green_confirm_bars"
    return False, ""
