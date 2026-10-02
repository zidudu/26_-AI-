@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo 먼저 01_setup.bat을 실행하세요.
 pause
 exit /b 1
)
echo 의미 검색 모듈에는 용량이 큰 PyTorch 패키지가 포함됩니다.
echo 프로그램을 먼저 종료하세요. 기본 키워드 검색에는 필요하지 않습니다.
choice /m "계속 설치할까요"
if errorlevel 2 exit /b 0
".venv\Scripts\python.exe" -m pip install -r requirements-semantic.txt
if errorlevel 1 goto fail
echo 설치 완료. 프로그램을 다시 켠 뒤 설정에서 색인 만들기를 누르세요.
echo 최초 색인은 모델 다운로드가 필요하며 CPU를 사용합니다.
pause
exit /b 0
:fail
echo 선택 모듈 설치 실패. 기본 기능은 계속 사용할 수 있습니다.
pause
exit /b 1
