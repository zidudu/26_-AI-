@echo off
set "YME_ROOT=%~dp0.."
set "YME_PYTHON=%YME_ROOT%\.venv\Scripts\python.exe"
set "PY_CMD="
py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto FOUND
python -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto FOUND
echo [ERROR] Install Python 3.10 or newer, then reopen this BAT.
exit /b 1
:FOUND
%PY_CMD% "%YME_ROOT%\prepare_env.py"
exit /b %ERRORLEVEL%
