# YouTube Media Library V8 beta.2 공유 패키지

Windows에서 YouTube 영상·음원·자막을 수집하고 태그·검색·재생으로 관리하는 로컬 웹 프로그램입니다.

[공유 ZIP 다운로드](./youtube_media_library_v8_beta2_share_20261001.zip?raw=true) · [구글 드라이브](https://drive.google.com/file/d/137JBAKkslnAyF3WR0fbidHYgGM0LLibW/view?usp=drivesdk) · [SHA-256](./SHA256SUMS.txt)

## 설치

1. Python 3.10 이상을 설치합니다.
2. ZIP을 쓰기 가능한 폴더에 압축 해제합니다.
3. `01_setup_beta.bat`으로 의존성과 본인의 로그인 계정을 설정합니다.
4. `run_v8.bat`으로 실행하고 `http://127.0.0.1:8765`에서 로그인합니다.
5. Windows 로그인 후 자동 시작은 `OPTIONAL_logon_only.bat`, 종료는 `stop_v8_beta.bat`입니다.

ZIP의 `SHARE_START.md`에 첫 실행 안내가 있습니다. Chrome 확장은 `chrome_extension`, 읽기 전용 AI MCP 서버는 `ai_plugin`에 포함됩니다. ChatGPT 웹 연결은 별도의 터널/HTTPS MCP 설정이 필요합니다.

## 포함된 수정

- 이미 켜진 자동 시작 서버를 BAT에서 재사용하고 브라우저 열기.
- URL 입력 Enter 제출 / Shift+Enter 줄바꿈.
- 목록·상세 사이 너비 조절, 넓은 목록에서 여러 열 표시, 사이드바 접기.
- 화면 크기에 맞춘 플레이어, 상단 다운로드 링크, 태그 및 패널 접기 개선.
- Chrome 현재 탭 전송 / 직접 링크 입력, AI MCP 검색·조회·자막 읽기.

개인 계정 설정·API 키·DB·영상·음원·자막·로그·가상환경은 포함하지 않습니다. Python 의존성은 첫 설치 때 다운로드합니다. 다운로드할 권한이 있는 자료에 사용하세요.

Windows/Python 3.13 회귀 검사: 76 passed, 5 skipped. ZIP CRC·파일별 SHA-256·새 폴더 압축 해제와 Python 문법 검사 통과. 패키지 80개 파일 / 168,328 bytes.
