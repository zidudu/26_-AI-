# Pin Archive 1.0.1 안정화본

Pinterest와 로컬 이미지를 메타데이터와 함께 보관하고 검색하는 Windows용 로컬 시각 레퍼런스 라이브러리입니다. 읽기 전용 stdio MCP를 통해 AI가 이미지 후보를 검색하고 실제 WebP 썸네일을 읽을 수 있습니다.

[소스와 샘플을 포함한 ZIP 다운로드](./Pin_Archive_1.0.1_stabilized.zip?raw=true) · [Google Drive ZIP](https://drive.google.com/file/d/1dxXW--SuVsXI_AUpeEGOYKCybNUCUMQY/view?usp=drivesdk) · [SHA-256](./SHA256SUMS.txt)

ChatGPT에서 여러 이미지를 한 번에 표시하려면 [갤러리 기능 추가 ZIP](./Pin_Archive_1.0.1_chatgpt_gallery.zip?raw=true)을 사용하세요. [Google Drive에서도 다운로드](https://drive.google.com/file/d/1X1hshUV-Xf6gD_oqeiSy4-7CzeAysihT/view?usp=drivesdk)할 수 있습니다. 이 업데이트에는 읽기 전용 `show_images` 도구와 최대 6장 갤러리 화면이 들어 있습니다. 신규 MCP 검사 12개와 실제 ChatGPT 화면에서 4장 표시를 확인했습니다. ChatGPT 앱과 로컬 터널 연결은 사용자 PC에서 별도로 설정해야 합니다.

Pinterest 로그인 화면이 멈추는 경우에는 [로그인 수정본 ZIP](./Pin_Archive_1.0.1_pinterest_login_fix.zip?raw=true)을 사용하세요. [Google Drive 다운로드](https://drive.google.com/file/d/1_37MbGQW7Q0ElryR9BU-RAzw8Lo4uxaN/view?usp=drivesdk)도 가능합니다. 이 ZIP에는 위 ChatGPT 갤러리 기능이 포함됩니다. Windows에서 Chrome/Edge를 선택하면 로그인 버튼이 자동화 브라우저 대신 전용 프로필의 일반 브라우저 창을 엽니다. 로그인 후 창을 닫으세요. 오프라인 회귀 검사 92개가 통과했으며, 실제 로그인된 Pinterest 수집은 아직 검증하지 않았습니다.

실제 보드에서 확인한 최신판은 [Pinterest 보드 수집 검증 ZIP](./Pin_Archive_1.0.1_pinterest_board_tested.zip?raw=true)입니다. [Google Drive 다운로드](https://drive.google.com/file/d/1AzLkvzFT6CfGRkkrZmMrWp-9NYTk6pPt/view?usp=drivesdk)도 가능합니다. 앞선 로그인·ChatGPT 갤러리 기능을 모두 포함하며, 사용자가 권한을 확인한 공개 보드에서 핀 1개를 실제 수집했습니다. 보드 미리보기 236×180 대신 핀 상세 화면에 제공된 1024×779 이미지를 저장하도록 보완했습니다. 계정의 모든 보드를 자동 동기화하는 기능은 없습니다.

ZIP을 완전히 푼 뒤 `pinterest_reference_v1/01_setup.bat`을 한 번 실행하고, 이후 `02_start.bat`으로 시작합니다. 기본 포트 8765가 사용 중이면 다음 빈 localhost 포트를 선택합니다. MCP와 크롤러 가져오기 예제는 실행 중인 서버의 포트를 자동 발견합니다.

기존 안정화본은 Windows Python 3.13에서 BAT 설치·실행, 88개 회귀 검사, 포트 충돌 시 실행, MCP 검색 및 실제 이미지 반환, 이미지 가져오기, Edge 데스크톱·모바일 화면을 확인했습니다. 실제 Pinterest 계정 로그인·수집, OpenAI/Ollama 추론, CLIP 모델 다운로드·추론은 미검증입니다. 상세 내용은 ZIP 안의 `docs/VALIDATION.md`를 참고하세요.

배포 ZIP에는 실행 중 생성되는 `data/`와 `.venv/`를 넣지 않았습니다. 사용 중인 `data/`는 이미지·메타데이터·로그인 프로필을 담으므로 업데이트할 때 보존하세요. 샘플 이미지의 출처와 이용 조건은 `THIRD_PARTY_NOTICES.md`를 참고하세요.
