@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

title YouTube Media Library V8 beta.2
call "%~dp0scripts\ensure_env.bat"
if errorlevel 1 goto FAILED
if exist "%~dp0config\owner.json" goto START
"%YME_PYTHON%" "%~dp0configure_beta.py"
if errorlevel 1 goto FAILED
:START
"%YME_PYTHON%" "%~dp0launcher.py"

set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
:FAILED
echo [ERROR] Setup failed. Read the message above.
pause
exit /b 1
