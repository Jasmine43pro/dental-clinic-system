@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUTF8=1
set TEMP=%~dp0.tmp
set TMP=%~dp0.tmp
"%~dp0.venv\Scripts\python.exe" -B "%~dp0scripts\run_local.py"
pause
