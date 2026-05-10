@echo off
REM Live-Log-Viewer fuer Cameron-Bot
REM Doppelklick = neues Fenster mit Live-Log
title Cameron-Bot Live Log
cd /d C:\Users\Szymon\ross-cameron\06_live_bot
echo ===============================================
echo CAMERON-BOT LIVE LOG
echo ===============================================
echo File: daemon.log
echo Heartbeat: alle 15 Min "ALIVE"
echo Stop:      dieses Fenster zumachen (Bot laeuft weiter)
echo ===============================================
echo.
powershell -NoProfile -Command "Get-Content daemon.log -Wait -Tail 30"
