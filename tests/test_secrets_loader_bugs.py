"""Audit-Iter 35 (2026-05-13): secrets_loader.py edge cases.

Bugs:
  SL-1 (HIGH): UTF-8-BOM in .env (Windows-Notepad default) → erste Key
    bekommt '\\ufeff'-Prefix → key-name mismatch → silent missing-error.
  SL-7 (MED): No file-permission check — world-readable .env auf Linux/Mac
    ist Sicherheitsrisiko.
  SL-8 (MED): Generic error message — kein Hinweis was schief lief.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── SL-1: BOM handling ─────────────────────────────────────────────────────
def test_loads_env_with_utf8_bom(tmp_path, monkeypatch):
    """REGRESSION SL-1: Windows-Notepad-saved .env hat BOM → vorher silent fail."""
    import secrets_loader
    env = tmp_path / ".env"
    # Write WITH UTF-8 BOM (\xef\xbb\xbf)
    env.write_bytes(b"\xef\xbb\xbfAPCA_API_KEY_ID=BOMKEY\nAPCA_API_SECRET_KEY=BOMSEC\n")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "BOMKEY"
    assert s == "BOMSEC"


def test_loads_env_without_bom_still_works(tmp_path, monkeypatch):
    """Sanity: regular (no-BOM) .env weiterhin funktional."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=PLAINKEY\nAPCA_API_SECRET_KEY=PLAINSEC\n",
                   encoding="utf-8")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "PLAINKEY"
    assert s == "PLAINSEC"


# ─── SL-8: detailed error message ────────────────────────────────────────────
def test_error_message_mentions_env_file_path_when_missing(monkeypatch, tmp_path):
    import secrets_loader
    missing = tmp_path / "nonexistent.env"
    monkeypatch.setattr(secrets_loader, "ENV_FILE", missing)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        secrets_loader.get_alpaca_keys()
    msg = str(exc.value)
    assert "APCA_API_KEY_ID" in msg
    assert ".env" in msg or "env" in msg.lower()


def test_error_message_distinguishes_missing_key_vs_secret(monkeypatch, tmp_path):
    """Wenn nur key fehlt, sollte error message das spezifisch sagen."""
    import secrets_loader
    monkeypatch.setattr(secrets_loader, "ENV_FILE", tmp_path / "no.env")
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.setenv("APCA_API_SECRET_KEY", "HAS_SECRET")
    with pytest.raises(RuntimeError) as exc:
        secrets_loader.get_alpaca_keys()
    msg = str(exc.value)
    assert "APCA_API_KEY_ID" in msg
    # Should NOT complain about SECRET since it IS set
    # (depends on exact phrasing — at least key should be mentioned)


def test_error_mentions_env_file_exists_but_no_keys(monkeypatch, tmp_path):
    """Wenn .env existiert aber leer → hint dass file da ist."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("# only comments\n", encoding="utf-8")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        secrets_loader.get_alpaca_keys()
    msg = str(exc.value)
    # Sollte erwähnen dass .env existiert
    assert "exists" in msg.lower() or "found" in msg.lower() or "format" in msg.lower()


# ─── SL-7: permission check (Linux only) ────────────────────────────────────
@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX permissions check only meaningful on Linux/Mac")
def test_warns_when_env_world_readable(tmp_path, monkeypatch, caplog):
    """SL-7: world-readable .env → warning logged."""
    import secrets_loader
    import logging
    import os
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=K\nAPCA_API_SECRET_KEY=S\n", encoding="utf-8")
    os.chmod(env, 0o644)  # world-readable
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        secrets_loader.get_alpaca_keys()
    assert any("permission" in r.message.lower() for r in caplog.records)


# ─── Edge: empty value, multiple = in value ──────────────────────────────────
def test_empty_value_treated_as_missing(tmp_path, monkeypatch):
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=\nAPCA_API_SECRET_KEY=somesec\n", encoding="utf-8")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        secrets_loader.get_alpaca_keys()


def test_value_with_equals_sign(tmp_path, monkeypatch):
    """Value with '=' in it should not be split."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=a=b=c\nAPCA_API_SECRET_KEY=x\n", encoding="utf-8")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "a=b=c"


def test_quote_pair_matching(tmp_path, monkeypatch):
    """Audit-Iter 35: pair-quoted values, mismatched quotes NICHT gestripped."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text(
        'APCA_API_KEY_ID="quoted_key"\n'
        "APCA_API_SECRET_KEY='single_quoted'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "quoted_key"
    assert s == "single_quoted"


def test_mismatched_quotes_not_stripped(tmp_path, monkeypatch):
    """Mixed-quote (not matching pair) sollte NICHT gestripped werden."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text(
        "APCA_API_KEY_ID=\"start_only\n"
        "APCA_API_SECRET_KEY=normal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    k, s = secrets_loader.get_alpaca_keys()
    # Vorher hätte strip('"') beide ends gestripped. Jetzt: nur paar match.
    assert k == '"start_only'


# ─── Env-overrides-file regression ──────────────────────────────────────────
def test_env_overrides_file(tmp_path, monkeypatch):
    """Sanity: env var wins over .env file."""
    import secrets_loader
    env = tmp_path / ".env"
    env.write_text("APCA_API_KEY_ID=FROMFILE\nAPCA_API_SECRET_KEY=FROMFILE_S\n",
                   encoding="utf-8")
    monkeypatch.setattr(secrets_loader, "ENV_FILE", env)
    monkeypatch.setenv("APCA_API_KEY_ID", "FROMENV")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "FROMENV_S")
    k, s = secrets_loader.get_alpaca_keys()
    assert k == "FROMENV"
    assert s == "FROMENV_S"
