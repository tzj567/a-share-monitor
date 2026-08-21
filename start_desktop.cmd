@echo off
cd /d "%~dp0"
python -m stock_monitor desktop
if errorlevel 1 pause
