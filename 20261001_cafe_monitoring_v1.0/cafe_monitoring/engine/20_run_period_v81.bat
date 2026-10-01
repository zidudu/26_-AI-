@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv_v6\Scripts\python.exe" goto missing
".venv_v6\Scripts\python.exe" -u "v8\period_run.py"
set "V8_ALPHA_EXIT=%ERRORLEVEL%"
echo.
if "%V8_ALPHA_EXIT%"=="2" echo [부분 완료] 위 결과와 PPT 내용을 검토하세요.
if "%V8_ALPHA_EXIT%"=="3" echo [메일 확인 필요] Outlook에서 발송 여부를 확인한 뒤 다음 기간 지정 실행을 진행하세요.
if "%V8_ALPHA_EXIT%"=="4" echo [실행 대기] 다른 작업이 끝난 뒤 다시 실행하세요.
if "%V8_ALPHA_EXIT%"=="1" echo [실패] 위 오류와 기간 지정 실행 로그를 확인하세요.
echo [종료] 예약은 유지됩니다. 아무 키나 누르면 이 창을 닫습니다.
pause >nul
exit /b %V8_ALPHA_EXIT%
:missing
echo 기존 naver_cafe 폴더에서 실행하세요. .venv_v6를 찾지 못했습니다.
pause
exit /b 1
