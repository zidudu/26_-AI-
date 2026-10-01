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

## 시스템 구조

실제 배포 코드 기준 구조입니다. 세로 모니터에서도 읽기 쉽도록 **모든 다이어그램을 위에서 아래로(TB)** 배치하고, 실행·수집·열람·연동을 각각 자세히 그렸습니다.

### 1. Windows 실행과 서버 재사용

```mermaid
flowchart TB
    Manual["사용자가 run_v8.bat 실행"]
    Logon["Windows 로그인<br/>선택 설치한 자동 시작 작업"]
    Launcher["launcher.py<br/>설정 읽기 · 인스턴스 잠금"]
    Config[("config/server.json<br/>포트 · 데이터/저장 폴더")]
    Check{"같은 서버가<br/>이미 실행 중인가?"}
    Health["runtime.json + /api/health<br/>같은 프로그램의 정상 서버 확인"]
    Reuse["기존 서버 재사용<br/>일반 실행은 브라우저 열기"]
    Start["Uvicorn + app.create_app<br/>기본 127.0.0.1:8765"]
    Background["서비스 실행<br/>pythonw · 브라우저 없이 대기"]
    Web["로그인 후 라이브러리 웹 화면"]

    Manual --> Launcher
    Logon --> Launcher
    Config -. 설정 .-> Launcher
    Launcher --> Check
    Check -->|실행 중| Health
    Health -->|정상| Reuse
    Check -->|실행 안 됨| Start
    Start -->|일반 실행| Web
    Start -->|자동 시작| Background
    Reuse --> Web

    classDef entry fill:#eaf2ff,stroke:#3971c6,color:#172b4d;
    classDef data fill:#fff6de,stroke:#c28c22,color:#493500;
    class Manual,Logon,Web entry;
    class Config data;
```

잠금은 있는데 서버가 응답하지 않거나 다른 프로그램이 포트를 쓰는 경우에는 오류를 표시합니다. 이미 실행 중인 정상 서버를 새로 띄우는 대신 재사용합니다.

### 2. 웹 서버, 작업 대기열과 수집 저장

```mermaid
flowchart TB
    UI["브라우저 UI · static/<br/>URL 입력 · 저장 옵션 · 태그<br/>Enter로 대기열 추가"]
    Security["인증·요청 검사 · remote_security.py<br/>로그인 세션 · Host/Origin · CSRF"]
    API["FastAPI · app.py<br/>URL/설정 검증 · 작업 API"]
    Manager["Manager · tasks.py<br/>SQLite 대기열에 등록<br/>한 번에 한 작업 실행 · 취소/재시도"]
    Worker["별도 Python 프로세스 · worker.py<br/>JSON 요청을 읽고 작업 종류 분기"]
    Extract["수집 · engine.extract_batch<br/>영상/재생목록 확장 · 메타데이터 조회"]
    Online["YouTube<br/>yt-dlp: 영상·음원·정보<br/>youtube-transcript-api: 자막"]
    FFmpeg["FFmpeg · engine.py / performance.py<br/>영상 병합 · 음원 변환<br/>브라우저/모바일 H.264 + AAC 재생본"]
    Files[("사용자가 지정한 저장 폴더<br/>영상 · 음원 · 원문 TXT · 타임스탬프 TXT<br/>자막 세그먼트 · 메타데이터 JSON · 썸네일")]
    Import["기존 폴더 가져오기 / 다시 읽기<br/>Store.scan"]
    Index["Store · library.py<br/>완성된 파일 경로·메타정보 등록<br/>태그 연결 · 기존 자료 인덱싱"]
    DB[("data/library.sqlite3<br/>자료 · 파일 · 태그 · 등록 폴더<br/>작업 상태 · 재생본 생성 이력")]
    Result["작업 결과·로그 저장<br/>data/jobs/*.request.json<br/>*.result.json · *.log"]
    Events["JobEvents · events.py<br/>진행률·상태·자료 변경 알림"]
    Refresh["/api/events → SSE로 화면 갱신<br/>연결 문제 시 작업 요약 폴링"]

    UI --> Security
    Security --> API
    API --> Manager
    Manager --> Worker
    Worker -->|extract| Extract
    Extract --> Online
    Online -->|다운로드·자막| FFmpeg
    Extract -->|자막·정보 직접 저장| Files
    FFmpeg --> Files
    Worker -->|import / rescan| Import
    Import --> Index
    Files --> Index
    Index --> DB
    Manager -->|작업 상태 기록| DB
    Worker --> Result
    Result -->|최종 상태 반영| Manager
    Manager --> Events
    Events --> Refresh

    classDef ui fill:#eaf2ff,stroke:#3971c6,color:#172b4d;
    classDef external fill:#ffeceb,stroke:#cb5b55,color:#54211e;
    classDef data fill:#fff6de,stroke:#c28c22,color:#493500;
    class UI,Refresh ui;
    class Online external;
    class Files,DB,Result data;
```

수집은 별도 프로세스에서 실행됩니다. 웹 서버는 검색·재생·대기열 조작 요청을 계속 처리하고, 작업 관리자는 진행률과 결과를 기록해 UI에 알립니다. 기존 폴더 가져오기는 실제 파일을 이동하지 않고 등록합니다.

### 3. 검색, 영상 재생과 파일 다운로드

```mermaid
flowchart TB
    View["라이브러리 웹 화면<br/>접는 사이드바 · 너비 조절 · 여러 열 목록<br/>상세 화면 · 화면에 맞춘 플레이어"]
    Query["/api/items · /api/items/{id}<br/>검색 · 종류/태그/즐겨찾기 필터 · 정렬"]
    Store["Store → SQLite<br/>자료 및 실제 파일 경로 확인"]
    Pick{"선택한 자료에서<br/>어떤 내용을 여는가?"}
    Text["/api/text/{file_id}<br/>필요할 때 TXT·타임스탬프 읽기"]
    Media["/media/{file_id}<br/>영상·음원 재생 / 저장된 파일 다운로드"]
    Cache["/api/thumbnails/{file_id}<br/>Pillow로 작은 썸네일 캐시"]
    Need["재생본 생성 요청<br/>preview / mobile_preview / mobile_batch"]
    Queue["작업 대기열 → worker → FFmpeg"]
    Preview[("data/previews · data/mobile_previews<br/>브라우저 호환 MP4 · 720p/480p MP4")]
    Render["선택한 자료의 상세 화면에 표시<br/>타임스탬프 탐색 · 상단 바로 다운로드"]

    View --> Query
    Query --> Store
    Store --> Pick
    Pick -->|자막·원문| Text
    Pick -->|영상·음원·파일| Media
    Pick -->|목록 이미지| Cache
    Media -->|호환 재생본 필요| Need
    Need --> Queue
    Queue --> Preview
    Preview -->|파일 등록 후 제공| Media
    Text --> Render
    Media --> Render
    Cache --> Render

    classDef ui fill:#eaf2ff,stroke:#3971c6,color:#172b4d;
    classDef data fill:#fff6de,stroke:#c28c22,color:#493500;
    class View,Render ui;
    class Preview data;
```

재생본은 원본과 별도로 만듭니다. 실제 영상·자막은 파일 시스템에 있고 SQLite는 검색·관리용 인덱스와 작업 상태를 보관합니다. 목록·텍스트 응답에는 ETag를 사용하고, 썸네일과 재생본은 다시 사용할 수 있도록 관리합니다.

### 4. Chrome 확장과 AI MCP 연결

```mermaid
flowchart TB
    Chrome["Chrome 확장 · chrome_extension/<br/>현재 탭 URL / 직접 링크 입력"]
    Add["로컬 웹 화면 열기<br/>http://127.0.0.1:8765/?add=URL"]
    Form["URL 수집 창에 주소 미리 입력<br/>사용자가 옵션 확인 후 대기열 추가"]
    WebAPI["인증된 웹 API → 수집 대기열"]

    Desktop["Codex / Claude 등<br/>로컬 MCP 클라이언트"]
    MCP["ai_plugin/mcp_server.py<br/>stdio MCP 서버"]
    Tools["읽기 전용 4개 도구<br/>yme_search_library · yme_get_item<br/>yme_read_text · yme_list_jobs"]
    Backend["LibraryBackend<br/>config/server.json에서 DB 위치 확인"]
    DB[("SQLite · mode=ro + query_only<br/>자료·파일·태그·작업 조회")]
    TXT[("저장된 원문/타임스탬프 TXT<br/>요청한 구간만 읽기")]

    ChatGPT["ChatGPT 웹"]
    Bridge["별도 MCP 터널 / HTTPS MCP 중계<br/>계정·권한·전송 방식 추가 설정 필요"]

    Chrome --> Add
    Add --> Form
    Form --> WebAPI
    Desktop -->|stdio 도구 호출| MCP
    MCP --> Tools
    Tools --> Backend
    Backend --> DB
    Backend --> TXT
    ChatGPT -. 추가 연결 필요 .-> Bridge
    Bridge -. 별도 구성 후 연결 .-> MCP

    classDef ui fill:#eaf2ff,stroke:#3971c6,color:#172b4d;
    classDef data fill:#fff6de,stroke:#c28c22,color:#493500;
    classDef optional fill:#f2edf9,stroke:#8a6bb3,color:#36264c,stroke-dasharray:5 5;
    class Chrome,Desktop,ChatGPT ui;
    class DB,TXT data;
    class Bridge optional;
```

- Chrome 확장은 수집 창에 URL을 전달합니다. 다운로드를 시작하는 단계는 웹 화면의 대기열 추가입니다.
- AI MCP는 웹 서버를 경유하지 않고 로컬 DB와 자막 파일을 직접 읽습니다. 현재 제공 도구로 다운로드를 실행하거나 파일을 변경하지 않습니다.
- ChatGPT 웹용 계정·터널 설정은 공유 패키지에 포함되지 않습니다. 점선은 추가 연결이 필요한 경로입니다. HTTPS 방식을 쓰려면 별도 HTTP MCP 구성이 필요합니다.
- 선택 가능한 원격 웹 접속은 Tailscale HTTPS 또는 Cloudflare 중계 설정으로 구성합니다. Cloudflare 경로는 관리 기능용이며 영상·음원 전송과 5 MB 초과 파일이 차단됩니다.

### 주요 코드 위치

ZIP 안에서 아래 파일을 확인할 수 있습니다.

| 구성 | 코드 | 역할 |
| --- | --- | --- |
| 실행·서버 재사용 | `launcher.py` | 설정, 잠금, 정상 서버 확인, Uvicorn 실행 |
| 웹 API·보안 | `app.py`, `yme/remote_security.py` | 로그인, 요청 검사, 자료·작업·파일 API |
| 작업 관리 | `yme/tasks.py`, `yme/worker.py`, `yme/events.py` | 대기열, 프로세스, 취소·재시도, SSE 알림 |
| 수집·변환 | `yme/engine.py`, `yme/performance.py` | YouTube 수집, FFmpeg, 썸네일·재생본 |
| 자료 저장소 | `yme/library.py` | SQLite, 파일 등록, 태그, 검색 |
| 웹 화면 | `static/index.html`, `static/app.js`, `static/styles.css` | 목록, 상세, 재생, URL 창, 너비·접기 |
| 브라우저 연동 | `chrome_extension/` | 현재 탭/직접 입력 URL 전달 |
| AI 연동 | `ai_plugin/mcp_server.py`, `ai_plugin/library_backend.py` | 읽기 전용 stdio MCP, DB·TXT 조회 |
