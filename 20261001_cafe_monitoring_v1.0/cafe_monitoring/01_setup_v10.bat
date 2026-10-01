@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
where py >nul 2>nul
if errorlevel 1 goto nopy
py -3 -m venv .venv
if errorlevel 1 goto failed
:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from v10 import __version__; print('Cafe Monitoring / Version 1.0 / expected tests: 75')"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
echo 설치 및 오프라인 검사가 끝났습니다. 02_start_v10.bat를 실행하세요.
pause
exit /b 0
:nopy
echo Python 3.11 이상이 필요합니다. 기존 Python 설치에서 py 실행기를 사용할 수 있는지 확인하세요.
pause
exit /b 1
:failed
echo 설치 또는 검사에 실패했습니다. 위 오류를 확인하세요.
pause
exit /b 1
