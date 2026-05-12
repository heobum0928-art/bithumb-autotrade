@echo off
cd /d C:\code\coinbase
start "" "C:\Users\허범근\AppData\Local\Programs\Python\Python313\python.exe" scripts\alt_monitor.py
timeout /t 3 /nobreak >nul
start "" "C:\Users\허범근\AppData\Local\Programs\Python\Python313\python.exe" scripts\tg_bot.py
