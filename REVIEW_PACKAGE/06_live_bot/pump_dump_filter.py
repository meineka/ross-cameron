"""Pump-Dump-Risk-Detection.

2026-05-12-Lesson: WOK (Score 4 375 → −4) und ODYS (3 043 → −0) waren klassische
Pump-Dump-Profile: extremer Pre-Market-Spike (>100 %) bei sehr hoher RVOL,
gefolgt von massivem Crash am Open. Cameron hat ODYS auch bei $13 gekauft
und $17 000 verloren wegen genau diesem Profil.

Erkennung: Wenn Pre-Market-Score > THRESHOLD → reduzierte Position-Size.
Score = RVOL × intraday_pct. Normale Cameron-Setups haben 10-500, Pump-Dumps
liegen oft >10 000.
"""
from __future__ import annotations

# Cameron-Erfahrung: Score >10k = extremes Pre-Market-Profil
PUMP_DUMP_SCORE_THRESHOLD = 10_000
PUMP_DUMP_SIZE_MULTIPLIER = 0.25  # nur ein Viertel der normalen Position


def is_pump_dump_risk(score: float, intraday_pct: float = 0.0,
                     rvol: float = 0.0) -> bool:
    """True wenn Setup-Profil pump-dump-verdächtig ist.

    Aktuell nur Score-basiert; spätere Versionen können Spread und
    Pre-Market-Time-of-Spike einbeziehen.
    """
    if score > PUMP_DUMP_SCORE_THRESHOLD:
        return True
    # Ergänzung: extreme Pct + extreme RVOL Kombination
    if intraday_pct > 100 and rvol > 50:
        return True
    return False


def size_multiplier(score: float, intraday_pct: float = 0.0,
                    rvol: float = 0.0) -> float:
    """Returns Multiplier für Position-Size. 1.0 = normal, <1 = reduziert."""
    if is_pump_dump_risk(score, intraday_pct, rvol):
        return PUMP_DUMP_SIZE_MULTIPLIER
    return 1.0
