"""Sicheres Laden von API-Keys. Reihenfolge:
   1. Environment-Variable
   2. .env-File (im 06_live_bot Ordner, gitignored)
Kein hardcoded Fallback mehr im Code.
"""
from __future__ import annotations
import os
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"


def _load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def get_alpaca_keys() -> tuple[str, str]:
    _load_env_file()
    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    sec = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    if not key or not sec:
        raise RuntimeError(
            "APCA_API_KEY_ID / APCA_API_SECRET_KEY missing — "
            "set env-vars or write .env in 06_live_bot/"
        )
    return key, sec
