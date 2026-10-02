@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Stop the beta server before updating. This does not update Tailscale or cloudflared.
if exist "%~dp0config\runtime.json" (
    echo The server may still be running. Use stop_v8_beta.bat first.
    pause
    exit /b 1
)
call "%~dp0scripts\ensure_env.bat"
if errorlevel 1 goto FAILED
"%YME_PYTHON%" "%~dp0prepare_env.py" --upgrade

set "RESULT=%ERRORLEVEL%"
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
:FAILED
echo [ERROR] Setup failed. Read the message above.
pause
exit /b 1
