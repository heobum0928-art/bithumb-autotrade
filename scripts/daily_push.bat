@echo off
cd /d C:\code\coinbase
git push origin main >> logs\daily_push.log 2>&1
