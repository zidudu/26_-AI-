@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title YouTube Media Library V8 beta.2
call "%~dp0scripts\ensure_env.bat"
if errorlevel 1 goto FAILED
"%YME_PYTHON%" "%~dp0configure_beta.py"

set "RESULT=%ERRORLEVEL%"
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
:FAILED
echo [ERROR] Setup failed. Read the message above.
pause
exit /b 1
