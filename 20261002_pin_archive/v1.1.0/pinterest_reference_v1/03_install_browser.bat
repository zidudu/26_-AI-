@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo 먼저 01_setup.bat을 실행하세요.
 pause
 exit /b 1
)
echo 수집용 Chromium을 다운로드합니다. 갤러리 사용만 할 때는 필요하지 않습니다.
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (echo 브라우저 설치 실패) else (echo 브라우저 설치 완료)
pause
