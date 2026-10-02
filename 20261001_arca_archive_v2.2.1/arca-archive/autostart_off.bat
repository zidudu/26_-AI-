@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\arca-archive.bat" 2>nul
echo 자동 시작 등록을 해제했습니다.
pause
