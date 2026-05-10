@echo off
REM Watchdog standalone starten
cd /d C:\Users\Szymon\ross-cameron\06_live_bot
set APCA_API_KEY_ID=PKBERNOMU23XEGRU5SPD3JZGDX
set APCA_API_SECRET_KEY=FZBBx9v8Pw7eaLRFD8wW51WNnVkWeWNkts2D7zRSaxaB
start "" /B pythonw watchdog.py
exit
