"""Loads .env from next to Jarvis.exe (or next to the sources) into os.environ. Import before reading any setting.
Fills gaps only: a blank template line, or a key already set in the real environment (e.g. $env: in PowerShell),
is never overwritten - .env is a fallback default, not an override."""
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

BASE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
ENV_PATH = BASE / ".env"

for _key, _val in dotenv_values(ENV_PATH, encoding="utf-8-sig").items():  # missing file -> {}; utf-8-sig eats Notepad's BOM
    if _key not in os.environ and _val and _val.strip():
        os.environ[_key] = _val.strip()
