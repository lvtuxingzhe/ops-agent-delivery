@echo off
cd /d D:\workbudy\work\ops-agent-delivery\code
.\backend\.venv\Scripts\python.exe backend\scripts\sync_prometheus_alerts.py --once >> "%TEMP%\ops-alert-sync.log" 2>&1