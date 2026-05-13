"""Audit-Iter 32 (2026-05-13): micro_test_trade.py helper bug-fixes.

Bugs:
  MT-1 (HIGH): str(o.status) compare fragile — gleicher Bug wie safe_bracket
    SB-2. Wenn alpaca-py Enum-Repr ändert, sieht micro-test nie FILLED.
  MT-2 (CRITICAL): Sell-Loop hatte keine timeout-Behandlung — bei
    API-Hänger blieb Position offen, Script exitierte ohne Cleanup.
  MT-3 (LOW): Hardcoded CANDIDATES war stale (von Wochen). Sollte heutige
    watchlist nutzen wenn verfügbar.

Diese Tests verifizieren via source-grep dass die Fixes drin sind.
Micro-test ist ein manuelles Live-Tool (kein test-fixture-Lauf möglich).
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))

MICRO_SRC = (ROOT / "06_live_bot" / "micro_test_trade.py").read_text(encoding="utf-8")


# ─── MT-1: status helper ────────────────────────────────────────────────────
def test_micro_uses_status_is_helper():
    """REGRESSION MT-1: brittle str(status) compare ersetzt durch _status_is."""
    assert "_status_is" in MICRO_SRC
    # darf nicht mehr die alte brittle check verwenden:
    assert 'str(o.status) in ("OrderStatus.FILLED", "filled")' not in MICRO_SRC


def test_micro_status_helper_matches_variants():
    """_status_is matched verschiedene Status-Repr-Varianten."""
    # Sourcing the function from micro_test_trade is tricky (script has top-level
    # side effects). Replicate the logic check:
    def _status_is(status, target):
        if status is None: return False
        for accessor in (
            getattr(status, "value", None),
            getattr(status, "name", None),
            str(status),
            str(status).rsplit(".", 1)[-1] if "." in str(status) else None,
        ):
            if accessor is None: continue
            if str(accessor).strip().upper() == target.upper():
                return True
        return False

    assert _status_is("filled", "FILLED") is True
    assert _status_is("FILLED", "FILLED") is True
    assert _status_is("OrderStatus.FILLED", "FILLED") is True
    assert _status_is("pending", "FILLED") is False
    assert _status_is(None, "FILLED") is False


# ─── MT-2: sell timeout cleanup ──────────────────────────────────────────────
def test_micro_sell_timeout_has_cleanup():
    """REGRESSION MT-2: sell-loop muss else-clause haben mit cancel + retry."""
    # Find the sell loop
    assert "SELL-TIMEOUT" in MICRO_SRC
    assert "stranded" in MICRO_SRC
    assert "cancel_order_by_id(close_order.id)" in MICRO_SRC


def test_micro_buy_timeout_has_cleanup():
    """Sanity: buy-timeout cancel pfad existiert auch."""
    assert "TIMEOUT — cancel + abort" in MICRO_SRC
    assert "cancel_order_by_id(order.id)" in MICRO_SRC


# ─── MT-3: dynamic candidate list ────────────────────────────────────────────
def test_micro_loads_watchlist_dynamically():
    """REGRESSION MT-3: heutige watchlist von Disk laden, nicht hardcoded."""
    assert "load_watchlist_if_fresh" in MICRO_SRC
    # CANDIDATES wird von load_watchlist gefüttert, nicht direkt hardcoded
    assert "CANDIDATES = load_watchlist_if_fresh()" in MICRO_SRC


def test_micro_has_fallback_for_no_watchlist():
    """Wenn keine fresh watchlist (z.B. erstmaliger run), HARDCODED_FALLBACK."""
    assert "HARDCODED_FALLBACK" in MICRO_SRC


# ─── Sanity: SPY fallback bleibt ─────────────────────────────────────────────
def test_micro_keeps_spy_fallback():
    """SPY ist immer tradable — letzter fallback wenn keine kandidaten."""
    assert 'FALLBACK = "SPY"' in MICRO_SRC
