@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 크롬 창이 열리면 "아카라이브" 탭에서 로그인하고 창을 닫으세요.
.venv\Scripts\python.exe -m arca_archive login
if errorlevel 1 pause
