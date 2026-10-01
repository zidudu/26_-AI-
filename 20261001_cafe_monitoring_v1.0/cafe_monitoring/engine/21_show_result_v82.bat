@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv_v6\Scripts\python.exe" "v8\result_window.py" --latest
if errorlevel 1 pause
