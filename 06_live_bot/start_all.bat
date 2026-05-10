@echo off
REM Cameron-Bot Auto-Start (Bot + Watchdog)
REM Wird automatisch nach Windows-Reboot gestartet (Startup-Folder)

cd /d C:\Users\Szymon\ross-cameron\06_live_bot

REM Env-Vars (falls setx noch nicht greift)
set APCA_API_KEY_ID=PKBERNOMU23XEGRU5SPD3JZGDX
set APCA_API_SECRET_KEY=FZBBx9v8Pw7eaLRFD8wW51WNnVkWeWNkts2D7zRSaxaB

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
echo Stop:    taskkill /F /IM python.exe
