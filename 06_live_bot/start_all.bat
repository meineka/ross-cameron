@echo off
REM Cameron-Bot Auto-Start (Bot + Watchdog)
REM Wird automatisch nach Windows-Reboot gestartet (Startup-Folder)
REM
REM Audit-Iter 36 (2026-05-13): hardcoded API-Keys entfernt (Security-Bug).
REM secrets_loader.py laedt automatisch aus 06_live_bot\.env (gitignored).
REM Hardcoded user path durch %~dp0 ersetzt (portable).

cd /d "%~dp0"

REM Use the launcher that resolves BOT_PYTHON/.venv and lets watchdog
REM start exactly one bot after dependency + position preflight.
start "" /B powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_watchdog.ps1" >> watchdog_launcher.log 2>&1

echo.
echo Watchdog gestartet im Hintergrund; er startet den Bot nach Preflight.
echo Logs:
echo   - daemon.log    (Bot-Output)
echo   - watchdog.log  (Watchdog-Output)
echo   - watchdog_launcher.log  (Launcher-Output)
echo.
echo Verify:  tasklist ^| findstr python
echo Stop:    nur den bot.py-Prozess killen via PID (nicht /IM python.exe — killed alle!)
echo          tasklist /V ^| findstr bot.py
echo          taskkill /F /PID ^<bot-pid^>
