@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
".venv_v6\Scripts\python.exe" -m unittest discover -s v8\tests -v
set "V8_EXIT=%ERRORLEVEL%"
pause
exit /b %V8_EXIT%
