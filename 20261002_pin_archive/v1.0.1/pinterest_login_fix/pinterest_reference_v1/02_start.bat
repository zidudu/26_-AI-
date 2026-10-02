@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo 먼저 01_setup.bat을 실행하세요.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" run.py %*
echo.
echo Pin Archive 서버가 종료되었습니다.
pause
