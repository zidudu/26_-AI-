@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title YouTube Media Library V8 beta.2
set "YME_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%YME_PYTHON%" goto FAILED
"%YME_PYTHON%" "%~dp0tunnel_runner.py" --stop

set "RESULT=%ERRORLEVEL%"
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
:FAILED
echo [ERROR] Setup failed. Read the message above.
pause
exit /b 1
