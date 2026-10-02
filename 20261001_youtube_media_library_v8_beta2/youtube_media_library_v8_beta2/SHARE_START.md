# 공유 패키지 첫 실행

이 ZIP은 Windows용 YouTube Media Library V8 beta.2 프로그램과 Chrome 확장, 읽기 전용 AI MCP 서버 소스입니다. Python 가상환경과 패키지는 첫 설치 때 다운로드합니다.

## 새 PC에서 시작

1. Python 3.10 이상을 설치합니다. 설치 후 터미널을 새로 열고 `py -3 --version`으로 확인합니다.
2. ZIP을 `C:\YouTubeMediaLibrary` 등 쓰기 가능한 폴더에 압축 해제합니다. ZIP 안에서 BAT를 직접 실행하지 마세요.
3. `01_setup_beta.bat`을 실행합니다. 인터넷 연결로 의존성을 설치한 뒤 저장 폴더와 본인의 로그인 계정을 설정합니다.
4. `run_v8.bat`을 실행합니다. 브라우저에서 `http://127.0.0.1:8765`가 열리면 본인이 설정한 계정으로 로그인합니다.
5. `URL 추가`에서 링크를 넣고 Enter로 대기열에 추가합니다. Shift+Enter는 줄바꿈입니다.

서버가 이미 켜져 있으면 `run_v8.bat`은 해당 서버의 화면을 엽니다. 종료는 `stop_v8_beta.bat`을 사용합니다.

## Windows 로그인 후 자동 시작

`OPTIONAL_logon_only.bat`을 실행하여 작업 스케줄러 등록 안내에 따라 설정합니다. 관리 권한이 필요하며 로그인 후에 서버가 시작됩니다. 등록 뒤에는 설치 폴더를 이동하지 마세요. 해제는 `remove_autostart.bat`입니다.

## Chrome 확장

Chrome의 `chrome://extensions`에서 개발자 모드를 켜고 `압축해제된 확장 프로그램을 로드합니다`로 `chrome_extension` 폴더를 선택합니다. 이 확장은 같은 PC의 기본 포트 8765 서버를 사용합니다. 현재 YouTube 탭을 보내거나 팝업에 링크를 직접 넣을 수 있습니다. 자세한 내용은 `chrome_extension/README.md`를 참고하세요.

## AI 연결

`ai_plugin/README.md`에서 별도 가상환경 설치와 Claude Desktop 등록 절차를 확인하세요. ChatGPT 웹은 별도의 터널 또는 HTTPS MCP 연결 설정이 필요합니다. ZIP을 설치하는 것만으로 AI 계정에 등록되지는 않습니다.

## 설치 범위

개인 로그인 설정, API 키, DB, 영상·음원·자막, 로그, 기존 가상환경은 포함하지 않습니다. 외부 접속은 본인의 Tailscale/Cloudflare 계정으로 별도 설정합니다. 다운로드할 권한이 있는 자료에 사용하세요.

일반 기능과 업데이트 안내는 `README.md`, 확인된 테스트와 한계는 `TESTING.md`, 의존성 정보는 `THIRD_PARTY.md`에 있습니다.
