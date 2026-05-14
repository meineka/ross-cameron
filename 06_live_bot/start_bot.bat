@echo off
REM Cameron-Bot Auto-Start
REM Wird automatisch nach Windows-Reboot gestartet (Startup-Folder)
REM
REM Audit-Iter 36 (2026-05-13): hardcoded API-Keys entfernt (Security-Bug).
REM secrets_loader.py laedt aus 06_live_bot\.env (gitignored).
REM Stelle sicher dass .env existiert mit APCA_API_KEY_ID + APCA_API_SECRET_KEY.

cd /d "%~dp0"

set "BOT_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" set "BOT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%BOT_PYTHON%" set "BOT_PYTHON=python"

REM Bot im Hintergrund starten — keys werden aus .env geladen
start "" /B "%BOT_PYTHON%" bot.py --daemon > daemon.log 2>&1

echo Bot gestartet im Hintergrund. Log: daemon.log
echo Stop: tasklist und taskkill /PID xxx
