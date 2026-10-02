@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo [Pin Archive] 기본 환경 설치
where py >nul 2>nul
if not errorlevel 1 (
 py -3 -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15), 'Python 3.11-3.14 is required'"
 if errorlevel 1 goto fail
 if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
) else (
 python -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15), 'Python 3.11-3.14 is required'"
 if errorlevel 1 goto fail
 if not exist ".venv\Scripts\python.exe" python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py" -v
if errorlevel 1 goto fail
echo.
echo 설치와 기본 검사가 끝났습니다. 02_start.bat을 실행하세요.
echo Pinterest 수집은 03_install_browser.bat 또는 설정의 Chrome/Edge 선택이 필요합니다.
pause
exit /b 0
:fail
echo.
echo 설치 또는 검사에 실패했습니다. 위 오류를 확인하세요.
echo Python 3.11~3.14 64비트와 인터넷 연결이 필요합니다. 권장: Python 3.13.
pause
exit /b 1
