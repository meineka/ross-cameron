"""Audit-Iter 13 (2026-05-12): pre_flight.py robustness.

Pre-Flight ist der Start-Gatekeeper. Wenn er Failures durchrutschen
lässt, startet der Bot mit kaputtem State und tradet "live".

Gefundene Bugs:
  PF-1 (HIGH): trading_blocked / account_blocked wurden nicht geprüft.
               Blocked account → Bot startet → jede Order rejected.
  PF-6 (HIGH): Kein min-equity-Check. Equity=$0 → Bot computiert
               0 Shares überall, tradet nicht aber wirkt "alive".
  PF-7: Empty api_key/secret → cryptische API-Exception statt klarer
        Fehlermeldung.
"""
from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── PF-7: Empty Keys ────────────────────────────────────────────────────────
def test_empty_api_key_fails_early_with_clear_message():
    from pre_flight import check_alpaca_auth
    ok, msg = check_alpaca_auth("", "secret")
    assert ok is False
    assert "leer" in msg or "empty" in msg.lower()


def test_empty_secret_fails_early():
    from pre_flight import check_alpaca_auth
    ok, msg = check_alpaca_auth("key", "")
    assert ok is False


def test_both_keys_present_proceeds_to_api_call():
    """Sanity: mit non-empty keys wird api aufgerufen (mock catches)."""
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient", side_effect=RuntimeError("X")):
        ok, msg = check_alpaca_auth("key", "secret")
    assert ok is False
    assert "FAIL" in msg


# ─── PF-1: Blocked Account ───────────────────────────────────────────────────
def test_account_blocked_fails():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = True
        acc.trading_blocked = False
        acc.equity = "10000"
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s")
    assert ok is False
    assert "account_blocked" in msg


def test_trading_blocked_fails():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = False
        acc.trading_blocked = True
        acc.equity = "10000"
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s")
    assert ok is False
    assert "trading_blocked" in msg


def test_both_unblocked_passes():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = False
        acc.trading_blocked = False
        acc.equity = "10000"
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s")
    assert ok is True


# ─── PF-6: Min-Equity ────────────────────────────────────────────────────────
def test_equity_below_minimum_fails():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = False
        acc.trading_blocked = False
        acc.equity = "100"  # below default 500 min
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s")
    assert ok is False
    assert "min" in msg.lower() or "equity" in msg.lower()


def test_equity_at_minimum_passes():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = False
        acc.trading_blocked = False
        acc.equity = "500.00"
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s", min_equity=500.0)
    assert ok is True


def test_equity_non_numeric_fails():
    from pre_flight import check_alpaca_auth
    with patch("alpaca.trading.client.TradingClient") as MTC:
        client = MagicMock()
        acc = MagicMock()
        acc.account_blocked = False
        acc.trading_blocked = False
        acc.equity = "garbage"
        client.get_account.return_value = acc
        MTC.return_value = client
        ok, msg = check_alpaca_auth("k", "s")
    assert ok is False


# ─── run_preflight: aggregate behavior ───────────────────────────────────────
def test_run_preflight_returns_false_on_auth_fail():
    from pre_flight import run_preflight
    with patch("pre_flight.check_alpaca_auth", return_value=(False, "auth fail")), \
         patch("pre_flight.check_ws_init", return_value=(True, "ws ok")):
        assert run_preflight("k", "s", skip_yfinance=True) is False


def test_run_preflight_returns_true_when_all_pass():
    from pre_flight import run_preflight
    with patch("pre_flight.check_alpaca_auth", return_value=(True, "ok")), \
         patch("pre_flight.check_ws_init", return_value=(True, "ws ok")):
        assert run_preflight("k", "s", skip_yfinance=True) is True


def test_run_preflight_tolerates_yfinance_fail():
    """yfinance ist nicht-kritisch — preflight darf trotzdem PASS returnen."""
    from pre_flight import run_preflight
    with patch("pre_flight.check_alpaca_auth", return_value=(True, "ok")), \
         patch("pre_flight.check_ws_init", return_value=(True, "ws ok")), \
         patch("pre_flight.check_yfinance", return_value=(False, "rate limit")):
        assert run_preflight("k", "s") is True
