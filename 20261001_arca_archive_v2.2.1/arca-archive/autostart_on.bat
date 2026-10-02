@echo off
chcp 65001 >nul
set "SRC=%~dp0start.bat"
set "DST=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\arca-archive.bat"
> "%DST%" echo @echo off
>> "%DST%" echo start "" /min "%SRC%"
echo Windows 로그인 시 arca-archive 서버가 최소화 창으로 자동 시작됩니다.
echo 등록 위치: %DST%
pause
