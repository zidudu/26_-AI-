@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [설치 필요] install.bat 를 먼저 실행하세요.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import pydantic_core, lxml, fastapi, playwright" >nul 2>nul
if errorlevel 1 (
  echo [환경 손상] 가상환경의 패키지가 Python 버전과 맞지 않습니다. install.bat 를 실행하면 자동으로 고칩니다.
  pause
  exit /b 1
)
echo arca-archive 서버를 시작합니다. 창을 닫으면 수집도 멈춥니다.
start "" http://127.0.0.1:8766/
.venv\Scripts\python.exe -m arca_archive serve
pause
