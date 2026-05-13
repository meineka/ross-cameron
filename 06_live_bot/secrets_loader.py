"""Sicheres Laden von API-Keys. Reihenfolge:
   1. Environment-Variable
   2. .env-File (im 06_live_bot Ordner, gitignored)
Kein hardcoded Fallback mehr im Code.

Audit-Iter 35 (2026-05-13) — Bug-Fixes SL-1/SL-7/SL-8:
  SL-1: UTF-8-BOM in .env (Windows-Notepad-Default) führte zu silent
        fail. Erste Key war '\\ufeffAPCA_API_KEY_ID' statt 'APCA_API_KEY_ID'.
        Jetzt: encoding="utf-8-sig" strippt BOM automatisch.
  SL-7: Optional file-permission check (Linux) — warnt wenn .env world-
        readable ist (sicherheitskritisch).
  SL-8: Detail-Logger statt silent. Hilft Debugging wenn keys missing.
"""
from __future__ import annotations
import logging
import os
import stat
from pathlib import Path

log = logging.getLogger("secrets_loader")

ENV_FILE = Path(__file__).parent / ".env"


def _check_file_permissions(path: Path) -> None:
    """Audit-Iter 35 (SL-7): warn if .env is world-readable on Linux/Mac.
    Windows hat keine vergleichbare POSIX-permission, skip dort."""
    if os.name == "nt":
        return
    try:
        st = path.stat()
        # 0o077 = group/world readable/writable bits
        if st.st_mode & 0o077:
            log.warning(
                ".env file has loose permissions (mode %o). "
                "Consider: chmod 600 %s",
                stat.S_IMODE(st.st_mode), path,
            )
    except OSError:
        pass


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        log.debug("no .env file at %s — relying on env vars only", ENV_FILE)
        return
    _check_file_permissions(ENV_FILE)
    # Audit-Iter 35 (SL-1): utf-8-sig strips BOM if present (Windows-Notepad
    # saves files with BOM by default — without sig, first key gets
    # '﻿'-prefix and silent-fails).
    try:
        raw = ENV_FILE.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        log.warning(".env read failed: %s — skipping", e)
        return
    n_loaded = 0
    for line_num, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        # Strip surrounding whitespace, then matching pair of quotes
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        if not k:
            log.warning(".env line %d: empty key, skipped", line_num)
            continue
        if k in os.environ:
            log.debug(".env line %d: %s already in env, skipped", line_num, k)
            continue
        os.environ[k] = v
        n_loaded += 1
    if n_loaded:
        log.debug("loaded %d keys from %s", n_loaded, ENV_FILE)


def get_alpaca_keys() -> tuple[str, str]:
    """Returns (key, secret). Raises RuntimeError mit detail-message wenn missing."""
    _load_env_file()
    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    sec = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    if not key or not sec:
        # Audit-Iter 35 (SL-8): diagnostic detail
        details = []
        if not key:
            details.append("APCA_API_KEY_ID missing")
        if not sec:
            details.append("APCA_API_SECRET_KEY missing")
        if not ENV_FILE.exists():
            details.append(f"(.env file not found at {ENV_FILE})")
        else:
            details.append(f"(.env exists at {ENV_FILE} but didn't supply keys "
                           f"— check format, BOM, or already-set env vars)")
        raise RuntimeError(" — ".join(details))
    return key, sec
