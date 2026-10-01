@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" -m v10
pause
exit /b
:missing
echo 먼저 01_setup_v10.bat를 실행하세요.
pause
