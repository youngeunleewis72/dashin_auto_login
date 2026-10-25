@echo off
setlocal
chcp 65001 >nul
NET SESSION >nul 2>&1
if errorlevel 1 (
    echo Administrator privileges are required. Run from an elevated terminal. 1>&2
    exit /b 1
)
cd /d "%~dp0"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment Python not found. 1>&2
    exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
".venv\Scripts\python.exe" -u "daily_runner.py"
set "collectorExitCode=%ERRORLEVEL%"
exit /b %collectorExitCode%
