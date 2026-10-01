# Pin Archive 1.1.0 — Pinterest 보드 반복 수집

[배포 ZIP 다운로드](./Pin_Archive_1.1.0_recurring_boards.zip?raw=true) · [Google Drive 다운로드](https://drive.google.com/file/d/1N6PkLCDsbqhlbrY12HuTibgtE9cYl-Qy/view?usp=drivesdk) · [SHA-256](./SHA256SUMS.txt)

기존 1.0.1의 로컬 이미지 라이브러리, ChatGPT MCP 갤러리, Pinterest 일반 Chrome 로그인 기능을 포함합니다. Pinterest 보드 URL과 저장할 로컬 보드를 등록하면 서버가 켜져 있는 동안 설정한 간격으로 반복 수집합니다. 반복 수집용 Chrome은 로그인된 전용 프로필을 사용하며 브라우저 창을 표시하지 않습니다. 서버 관리 창과 수동 로그인 창은 별개입니다.

반복 작업은 **보드에서 핀 발견 → 핀 상세 정보 기록 → 미완료 이미지 저장** 순서로 진행합니다. 핀 ID별 상태와 상세 JSON 변경 이력을 SQLite에 보관하고, 실패한 상세·이미지 작업은 대기 후 재시도합니다. 이미 저장한 이미지는 반복 실행에서 다시 다운로드하지 않습니다. Pinterest의 무한 스크롤을 설정한 탐색 상한까지 확인하며, 계정의 모든 보드 자동 발견 기능은 없습니다.

Windows Python 3.13에서 오프라인 회귀 검사 99개가 통과했습니다. 로그인된 공개 보드에서 창 없는 수집을 시험해 핀 53개를 발견하고 첫 예약 실행에서 이미지 4개를 새로 저장했습니다. 상세 페이지 1개는 시간 초과로 재시도 대기 상태였습니다. 장시간 무인 수집, 비공개 보드, 큰 보드의 전체 완주, 이미지 이용 권한은 이 시험으로 확인되지 않습니다. 자세한 기록은 ZIP 안의 `docs/VALIDATION.md`에 있습니다.

ZIP을 완전히 푼 뒤 `pinterest_reference_v1/01_setup.bat`을 한 번 실행하고 `02_start.bat`으로 서버를 시작하세요. 기존 설치를 업데이트할 때는 **사용 중인 `data/` 폴더를 보존**하세요. 이 폴더에 이미지, SQLite 데이터베이스와 Pinterest 로그인 프로필이 있습니다. 배포 ZIP에는 `data/`, `.venv/`, 시험 자료를 넣지 않았습니다.

[이전 1.0.1 배포본](../20261002_pin_archive_v1.0.1/)도 보관되어 있습니다.
