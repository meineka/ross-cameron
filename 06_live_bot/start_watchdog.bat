@echo off
REM Watchdog standalone starten
REM Audit-Iter 36: hardcoded keys raus, secrets_loader macht .env
cd /d "%~dp0"
start "" /B pythonw watchdog.py
exit
