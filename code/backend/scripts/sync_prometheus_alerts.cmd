@echo off
cd /d "%~dp0..\.."
.\backend\.venv\Scripts\python.exe backend\scripts\sync_prometheus_alerts.py --once >> "%TEMP%\ops-alert-sync.log" 2>&1