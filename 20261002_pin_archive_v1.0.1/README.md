# Pin Archive 1.0.1 안정화본

Pinterest와 로컬 이미지를 메타데이터와 함께 보관하고 검색하는 Windows용 로컬 시각 레퍼런스 라이브러리입니다. 읽기 전용 stdio MCP를 통해 AI가 이미지 후보를 검색하고 실제 WebP 썸네일을 읽을 수 있습니다.

[소스와 샘플을 포함한 ZIP 다운로드](./Pin_Archive_1.0.1_stabilized.zip?raw=true) · [Google Drive ZIP](https://drive.google.com/file/d/1dxXW--SuVsXI_AUpeEGOYKCybNUCUMQY/view?usp=drivesdk) · [SHA-256](./SHA256SUMS.txt)

ZIP을 완전히 푼 뒤 `pinterest_reference_v1/01_setup.bat`을 한 번 실행하고, 이후 `02_start.bat`으로 시작합니다. 기본 포트 8765가 사용 중이면 다음 빈 localhost 포트를 선택합니다. MCP와 크롤러 가져오기 예제는 실행 중인 서버의 포트를 자동 발견합니다.

Windows Python 3.13에서 BAT 설치·실행, 88개 회귀 검사, 포트 충돌 시 실행, MCP 검색 및 실제 이미지 반환, 이미지 가져오기, Edge 데스크톱·모바일 화면을 확인했습니다. 실제 Pinterest 계정 로그인·수집, OpenAI/Ollama 추론, CLIP 모델 다운로드·추론은 미검증입니다. 상세 내용은 ZIP 안의 `docs/VALIDATION.md`를 참고하세요.

배포 ZIP에는 실행 중 생성되는 `data/`와 `.venv/`를 넣지 않았습니다. 사용 중인 `data/`는 이미지·메타데이터·로그인 프로필을 담으므로 업데이트할 때 보존하세요. 샘플 이미지의 출처와 이용 조건은 `THIRD_PARTY_NOTICES.md`를 참고하세요.
