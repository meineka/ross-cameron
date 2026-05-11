"""Regression-Test: alpaca-py StockDataStream-Init braucht DataFeed-Enum.

Verhindert Wiederkehr des 12:27-CET-Bugs vom 2026-05-11 wo `feed="iex"` (string)
einen AttributeError 'str' object has no attribute 'value' warf und WS-Loop
in Endlos-Reconnect ging.
"""
from alpaca.data.live import StockDataStream
from alpaca.data.enums import DataFeed


def test_stockdatastream_accepts_enum_feed():
    ws = StockDataStream("dummy_key", "dummy_secret", feed=DataFeed.IEX)
    assert ws is not None
    assert "iex" in ws._endpoint


def test_bot_uses_enum_not_string():
    """bot.py muss DataFeed.IEX importieren und benutzen, nie 'iex' string."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    assert "from alpaca.data.enums import DataFeed" in src, "DataFeed-Import fehlt"
    assert "feed=DataFeed.IEX" in src, "feed=DataFeed.IEX nicht verwendet"
    assert 'feed="iex"' not in src, "alter String-Feed noch da"
