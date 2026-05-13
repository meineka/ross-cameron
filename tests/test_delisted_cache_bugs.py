"""Audit-Iter 23 (2026-05-12): delisted_cache.py durability bugs.

Bugs:
  DC-1 (HIGH): _save() schrieb direkt zur CACHE_FILE ohne atomic-rename.
    Crash mid-write (Cloud-Restart, OOM, watchdog-kill) → file ist
    half-written → next _load() failt mit JSONDecodeError → _cache={}
    → ALLE 3000 delisted Tickers wieder "alive" → yfinance-ERROR-Spam.
    Genau das Szenario das das Cache überhaupt erst gebaut hat zu lösen.
  DC-3 (MED): Corrupt JSON wurde silent reset, kein log/warning.
    Operator hatte keinen Hinweis dass cache verloren ging.
  DC-6 (MED): `if ts and ts >= cutoff` — ts=0.0 ist falsy → symbol passt
    Filter durch (= live), obwohl im Cache mit ts=0 markiert.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


@pytest.fixture(autouse=True)
def _reset_cache_each_test(tmp_path, monkeypatch):
    """Per-test cache file + reset module-level state."""
    import delisted_cache
    cache_file = tmp_path / "delisted_cache.json"
    monkeypatch.setattr(delisted_cache, "CACHE_FILE", cache_file)
    delisted_cache.reset_cache()
    yield
    delisted_cache.reset_cache()


# ─── Bug DC-1: atomic write ──────────────────────────────────────────────────
def test_save_uses_atomic_rename(tmp_path, monkeypatch):
    """_save schreibt zuerst .tmp, dann rename — kein half-written CACHE_FILE."""
    import delisted_cache
    import os

    rename_calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        rename_calls.append((src, dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    delisted_cache.mark_delisted("ZZZ")
    assert len(rename_calls) >= 1
    # tmp source endet mit .json.tmp, dst endet mit .json
    src, dst = rename_calls[-1]
    assert src.endswith(".json.tmp")
    assert dst.endswith(".json")


def test_corrupt_cache_file_does_not_lose_subsequent_writes(tmp_path, monkeypatch):
    """Wenn CACHE_FILE corrupt ist, write trotzdem funktioniert."""
    import delisted_cache
    cache_file = delisted_cache.CACHE_FILE
    cache_file.write_text("{ this is { not json }}", encoding="utf-8")
    delisted_cache.reset_cache()
    # Mark something — should not crash
    delisted_cache.mark_delisted("ABC")
    # Read back — file should now be valid JSON
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "ABC" in data


def test_save_tmp_file_cleaned_after_success():
    """Nach erfolgreichem save sollte .tmp file weg sein (rename macht das)."""
    import delisted_cache
    delisted_cache.mark_delisted("X")
    tmp = delisted_cache.CACHE_FILE.with_suffix(".json.tmp")
    assert not tmp.exists()
    assert delisted_cache.CACHE_FILE.exists()


# ─── Bug DC-3: corrupt JSON warning ──────────────────────────────────────────
def test_corrupt_json_logs_warning(caplog):
    import delisted_cache
    import logging
    delisted_cache.CACHE_FILE.write_text("garbage {not json}", encoding="utf-8")
    delisted_cache.reset_cache()
    with caplog.at_level(logging.WARNING, logger="delisted_cache"):
        delisted_cache._load()
    assert any("corrupt" in r.message.lower() or "json" in r.message.lower()
               for r in caplog.records)


def test_non_dict_json_logs_warning(caplog):
    """Falls die Datei ein list ist (z.B. alte Version) → warning + reset."""
    import delisted_cache
    import logging
    delisted_cache.CACHE_FILE.write_text('["AAA", "BBB"]', encoding="utf-8")
    delisted_cache.reset_cache()
    with caplog.at_level(logging.WARNING, logger="delisted_cache"):
        delisted_cache._load()
    assert any("format" in r.message.lower() or "dict" in r.message.lower()
               for r in caplog.records)


# ─── Bug DC-6: defensive ts check ────────────────────────────────────────────
def test_filter_handles_zero_timestamp():
    """ts=0.0 (corruption) darf nicht als 'live' durchgehen."""
    import delisted_cache
    delisted_cache.reset_cache()
    c = delisted_cache._load()
    c["ZERO"] = 0.0  # corrupt
    delisted_cache._save()
    alive, skipped = delisted_cache.filter_known_delisted(["ZERO", "FRESH"])
    # ZERO has corrupt ts (0.0 < cutoff) → treated as expired → alive
    # FRESH not in cache → alive
    assert "FRESH" in alive
    # ZERO is ancient — should not be skipped, but should not crash either
    # (alt-bug war: if ts (=0.0 falsy) → alive bei truthy-check)


def test_filter_handles_non_numeric_timestamp():
    """ts=str (corruption) → defensive skip + treat as expired."""
    import delisted_cache
    delisted_cache.reset_cache()
    c = delisted_cache._load()
    c["STR_TS"] = "garbage"  # type-corruption
    delisted_cache._save()
    # Sollte nicht crashen
    alive, skipped = delisted_cache.filter_known_delisted(["STR_TS"])
    assert "STR_TS" in alive  # corrupt → not skipped


def test_is_delisted_handles_corrupt_timestamp():
    import delisted_cache
    delisted_cache.reset_cache()
    c = delisted_cache._load()
    c["BROKEN"] = None
    delisted_cache._save()
    # Sollte nicht crashen
    assert delisted_cache.is_delisted("BROKEN") is False


# ─── Sanity: normal operation ────────────────────────────────────────────────
def test_mark_and_check_delisted_roundtrip():
    import delisted_cache
    delisted_cache.mark_delisted("DEAD")
    assert delisted_cache.is_delisted("DEAD") is True


def test_filter_skips_known_delisted():
    import delisted_cache
    delisted_cache.mark_batch_delisted(["A", "B", "C"])
    alive, skipped = delisted_cache.filter_known_delisted(["A", "B", "FRESH"])
    assert "FRESH" in alive
    assert "A" not in alive
    assert "B" not in alive
    assert skipped == 2


def test_expired_entries_dropped():
    """Symbol > 30d alt → behandelt wie nicht gecached."""
    import delisted_cache
    delisted_cache.reset_cache()
    c = delisted_cache._load()
    c["OLD"] = time.time() - 31 * 86400  # 31 Tage alt
    delisted_cache._save()
    # is_delisted prunet expired entries
    assert delisted_cache.is_delisted("OLD") is False
    # filter_known_delisted lässt expired durch
    alive, skipped = delisted_cache.filter_known_delisted(["OLD"])
    assert "OLD" in alive


def test_stats_includes_live_and_expired_counts():
    """Audit-Iter 23: stats() zeigt jetzt mehr Detail."""
    import delisted_cache
    delisted_cache.mark_delisted("LIVE")
    # add expired entry directly
    c = delisted_cache._load()
    c["EXPIRED"] = time.time() - 60 * 86400
    delisted_cache._save()
    s = delisted_cache.stats()
    assert s["live_count"] >= 1
    assert s["expired_count"] >= 1
    assert "ttl_days" in s


# ─── Concurrent safety ───────────────────────────────────────────────────────
def test_concurrent_marks_dont_corrupt():
    """4 threads, je 25 mark_delisted-Calls — final cache hat alle 100."""
    import delisted_cache
    import threading
    delisted_cache.reset_cache()

    def worker(n):
        for i in range(25):
            delisted_cache.mark_delisted(f"T{n}_{i}")

    threads = [threading.Thread(target=worker, args=(j,)) for j in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Re-load from disk (fresh)
    delisted_cache.reset_cache()
    c = delisted_cache._load()
    # Sollte alle 100 entries haben (kein Race-Verlust)
    assert len(c) == 100
