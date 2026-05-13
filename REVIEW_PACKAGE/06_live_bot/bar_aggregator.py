"""1-Min → N-Min Bar-Aggregator (Cameron-Standard: 5-Min).

Alpaca WS liefert 1-Min-Bars. Cameron's Bull-Flag-Pattern + Pole/Flag-
Schwellwerte (POLE_MIN_MOVE_PCT=5%, all-green-pole) sind aber für
5-Min-Charts kalibriert. Auf 1-Min-Bars erfüllt fast nichts die Kriterien
(siehe Audit-Iter post-2026-05-13: 0 Entries auf 1275 live 1-Min-Bars).

Lösung: Aggregator sammelt 1-Min-Bars in 5-Min-Buckets und emittiert
einen aggregierten Bar wenn der Bucket komplett ist (= nächste 1-Min-Bar
gehört zum nächsten Bucket).

Wall-Clock-Boundaries: 9:30, 9:35, 9:40, ... (statt rolling-window).
Konsistent mit Cameron's tatsächlichem Chart-Behaviour.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional


class BarAggregator:
    """Per-symbol 1-Min → N-Min Aggregation.

    Usage:
        agg = BarAggregator(bucket_minutes=5)
        for bar in stream:  # 1-Min-Bars
            agg5min = agg.add(symbol, bar)  # returns aggregated bar or None
            if agg5min is not None:
                handle_bar(symbol, agg5min)

    Semantik:
        - add() returnt den FERTIGEN vorherigen 5-min-Bar wenn der neue
          1-Min-Bar in einen neuen Bucket fällt (= Bucket abgeschlossen).
        - flush() emittiert den AKTUELLEN partial Bucket (nutzbar bei
          HARD_FLAT oder Position-Management das nicht warten kann).
    """

    def __init__(self, bucket_minutes: int = 5):
        if bucket_minutes <= 0:
            raise ValueError(f"bucket_minutes must be > 0, got {bucket_minutes}")
        if 60 % bucket_minutes != 0:
            raise ValueError(
                f"bucket_minutes must evenly divide 60 (got {bucket_minutes})"
            )
        self.bucket_min = bucket_minutes
        # symbol -> list of 1-min bars in current bucket
        self.buffers: dict[str, list[dict]] = {}

    def _bucket_start(self, ts: datetime) -> datetime:
        """Floor timestamp to N-min bucket boundary (UTC-naive logic)."""
        minute = (ts.minute // self.bucket_min) * self.bucket_min
        return ts.replace(minute=minute, second=0, microsecond=0)

    def _aggregate(self, bars: list[dict], bucket_start: datetime) -> dict:
        """OHLCV merge of 1-min bars into one N-min bar."""
        return {
            "open": float(bars[0]["open"]),
            "high": max(float(b["high"]) for b in bars),
            "low": min(float(b["low"]) for b in bars),
            "close": float(bars[-1]["close"]),
            "volume": sum(float(b["volume"]) for b in bars),
            "timestamp": bucket_start,
        }

    def add(self, symbol: str, bar: dict) -> Optional[dict]:
        """Add a 1-min bar. Returns aggregated N-min bar if a bucket just
        completed (= this bar belongs to a NEW bucket), else None."""
        ts = bar.get("timestamp")
        if ts is None:
            return None
        try:
            new_bucket = self._bucket_start(ts)
        except (AttributeError, TypeError, ValueError):
            return None

        buf = self.buffers.setdefault(symbol, [])
        emitted = None

        if buf:
            existing_bucket = self._bucket_start(buf[0]["timestamp"])
            if existing_bucket != new_bucket:
                # Bucket-Transition → emit completed bucket
                emitted = self._aggregate(buf, existing_bucket)
                self.buffers[symbol] = []
                buf = self.buffers[symbol]

        # Add new bar to current bucket
        buf.append(bar)
        return emitted

    def flush(self, symbol: str) -> Optional[dict]:
        """Emit current partial bucket and clear buffer for symbol."""
        buf = self.buffers.get(symbol)
        if not buf:
            return None
        bucket_start = self._bucket_start(buf[0]["timestamp"])
        emitted = self._aggregate(buf, bucket_start)
        self.buffers[symbol] = []
        return emitted

    def flush_all(self) -> dict[str, dict]:
        """Emit all partial buckets across all symbols (e.g. at HARD_FLAT)."""
        out = {}
        for sym in list(self.buffers.keys()):
            emitted = self.flush(sym)
            if emitted is not None:
                out[sym] = emitted
        return out

    def reset(self, symbol: Optional[str] = None) -> None:
        """Clear buffer for one symbol or all (daily reset)."""
        if symbol is None:
            self.buffers.clear()
        else:
            self.buffers.pop(symbol, None)

    def buffer_size(self, symbol: str) -> int:
        return len(self.buffers.get(symbol, []))
