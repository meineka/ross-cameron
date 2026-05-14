@echo off
REM Watchdog standalone starten
REM Audit-Iter 36: hardcoded keys raus, secrets_loader macht .env
cd /d "%~dp0"
start "" /B powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_watchdog.ps1" >> watchdog_launcher.log 2>&1
exit
