@echo off
setlocal
set PYTHONUTF8=1
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "from v10 import __version__; print('Cafe Monitoring / Version 1.0 / expected tests: 75')"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
echo 오프라인 검사를 통과했습니다. 02_start_v10.bat를 실행하세요.
pause
exit /b 0
:failed
echo 검사에 실패했습니다. 위 오류 내용을 확인하세요.
pause
exit /b 1
