@echo off
setlocal
cd /d "%~dp0"
title YouTube Media Library - Private 8443
call "%~dp0scripts\ensure_env.bat"
if errorlevel 1 goto FAILED
"%~dp0.venv\Scripts\python.exe" "%~dp0connect_private_fast.py"
set "RC=%ERRORLEVEL%"
pause
exit /b %RC%
:FAILED
echo [ERROR] Environment setup failed.
pause
exit /b 1
