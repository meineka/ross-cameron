@echo off
REM Cameron-Bot Auto-Start
REM Wird automatisch nach Windows-Reboot gestartet (Startup-Folder)

cd /d C:\Users\Szymon\ross-cameron\06_live_bot

REM Env-Vars setzen (falls noch nicht durch setx geladen)
set APCA_API_KEY_ID=PKBERNOMU23XEGRU5SPD3JZGDX
set APCA_API_SECRET_KEY=FZBBx9v8Pw7eaLRFD8wW51WNnVkWeWNkts2D7zRSaxaB

REM Bot im Hintergrund starten
start /B python bot.py --daemon > daemon.log 2>&1

echo Bot gestartet im Hintergrund. Log: daemon.log
echo Stop: tasklist und taskkill /PID xxx
