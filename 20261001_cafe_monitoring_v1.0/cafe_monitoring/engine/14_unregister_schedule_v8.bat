@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv_v6\Scripts\python.exe" goto missing
".venv_v6\Scripts\python.exe" -u "v8\main.py" unregister
set "V8_EXIT=%ERRORLEVEL%"
if "%V8_EXIT%"=="2" echo Partial completion. Read the run status above.
if "%V8_EXIT%"=="3" echo Mail status needs review. Do not resend before checking Outlook.
if "%V8_EXIT%"=="1" echo Failed. Existing files are preserved.
pause
exit /b %V8_EXIT%
:missing
echo V6 Python not found. Install V8 into the existing naver_cafe folder.
pause
exit /b 1
