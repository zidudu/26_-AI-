@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo 먼저 01_setup.bat을 실행하세요.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" mcp_bridge.py --print-config %*
echo.
echo 위 설정은 로컬 stdio MCP를 지원하는 AI 도구용입니다.
echo ChatGPT 웹에는 localhost를 직접 붙여서 연결할 수 없습니다.
pause
