@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [arca-archive] Python 가상환경을 만들고 의존성을 설치합니다.

rem 기존 .venv 가 있으면 그대로 두되, 안의 해석기와 패키지가 맞지 않으면(다른 Python 버전으로 덧씌워진 경우) 새로 만듭니다.
if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe -c "import pydantic_core, lxml, fastapi, playwright" >nul 2>nul
  if not errorlevel 1 (
    echo 기존 가상환경이 정상입니다. 의존성만 확인합니다.
    .venv\Scripts\python.exe -m pip install -q -r requirements.txt
    goto :verify
  )
  echo 기존 가상환경의 패키지가 현재 Python 과 맞지 않아 새로 만듭니다.
  rmdir /s /q .venv
)

rem 1) uv 가 있으면 uv 로 Python 3.11 환경 생성(가장 확실). 2) 없으면 py 런처, 3) 마지막으로 python.
where uv >nul 2>nul
if not errorlevel 1 (
  uv venv .venv --python 3.11 && uv pip install -p .venv\Scripts\python.exe -r requirements.txt
  goto :verify
)
where py >nul 2>nul && (py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv) || python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo Python 3.11 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip >nul
.venv\Scripts\python.exe -m pip install -r requirements.txt

:verify
.venv\Scripts\python.exe -c "import pydantic_core, lxml, fastapi, playwright, httpx; import sys; print('Python', sys.version.split()[0], '/ 패키지 확인 완료')"
if errorlevel 1 (
  echo 설치 확인에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요.
  pause
  exit /b 1
)
if not exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  echo Chrome이 없어 Playwright Chromium을 설치합니다. 설치 후 config\settings.json의 browser.channel을 "chromium"으로 바꾸세요.
  .venv\Scripts\python.exe -m playwright install chromium
)
echo.
echo 설치 완료. start.bat 로 서버를 시작하세요.
pause
