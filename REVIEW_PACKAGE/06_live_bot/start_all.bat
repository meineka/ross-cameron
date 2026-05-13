@echo off
REM Cameron-Bot Auto-Start (Bot + Watchdog)
REM Wird automatisch nach Windows-Reboot gestartet (Startup-Folder)
REM
REM Audit-Iter 36 (2026-05-13): hardcoded API-Keys entfernt (Security-Bug).
REM secrets_loader.py laedt automatisch aus 06_live_bot\.env (gitignored).
REM Hardcoded user path durch %~dp0 ersetzt (portable).

cd /d "%~dp0"

REM Bot im Hintergrund (detached process)
start "" /B python bot.py --daemon > daemon.log 2>&1

REM Watchdog im Hintergrund
start "" /B python watchdog.py > watchdog.log 2>&1

echo.
echo Bot + Watchdog gestartet im Hintergrund.
echo Logs:
echo   - daemon.log    (Bot-Output)
echo   - watchdog.log  (Watchdog-Output)
echo.
echo Verify:  tasklist ^| findstr python
echo Stop:    nur den bot.py-Prozess killen via PID (nicht /IM python.exe — killed alle!)
echo          tasklist /V ^| findstr bot.py
echo          taskkill /F /PID ^<bot-pid^>
