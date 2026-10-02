# arca-archive

아카라이브(arca.live) 채널, 디시인사이드 공개 마이너 갤러리, 네이버 카페 어댑터를 제공하는 게시글·미디어 보관 시스템입니다. 현재 버전은 **2.2.1**입니다.
2.1.0은 글·이미지 북마크와 필터 가능한 썸네일 보관함을 추가합니다. [사용법과 검증 기록](docs/RELEASE_V2.1.0.md)을 참고하세요.
실제 검증 결과와 지원 범위는 [2.0.0 검증 보고서](docs/RELEASE_V2.0.0.md)를 참고하세요. 네이버 회원 전용 본문의 실제 검증은 완료되지 않았습니다.
사이트별 차이는 `arca_archive/sites/` 어댑터가 맡고, 파이프라인·저장소·GUI는 공통입니다.
설계 결정과 조사 결과는 [docs/DESIGN.md](docs/DESIGN.md)에 있습니다.

## 빠른 시작 (Windows)

1. Python 3.11 이상과 Chrome이 설치되어 있어야 합니다.
2. `install.bat` 더블클릭 → 가상환경(.venv) 생성과 의존성 설치.
3. `start.bat` 더블클릭 → 서버 시작, 브라우저에 http://127.0.0.1:8766/ 가 열립니다. 콘솔 창을 닫으면 수집도 멈춥니다.

첫 실행 시 `config/settings.json`과 `data/` 폴더가 생성되고 채널 `ailove`가 등록됩니다.
Chrome이 없으면 install.bat이 Playwright Chromium을 설치하며, 이 경우 설정의 `browser.channel`을 `chromium`으로 바꿉니다.
수동 설치는 `python -m venv .venv` 후 `.venv\Scripts\pip install -r requirements.txt` 입니다.

## 로그인이 필요한 채널 (ailove 등)

`login.bat` 또는 GUI 버튼으로 열리는 창은 자동화 없이 실행되는 일반 Chrome이라 로그인 캡챠가 정상 표시됩니다.

민감 채널은 비로그인 상태에서는 목록에 공지만 보이고 게시글은 HTTP 451로 막힙니다. 이런 채널은 **전용 크롬 프로필의 로그인 세션**으로 수집합니다.

1. GUI → **브라우저 세션** → **로그인 브라우저 열기**
2. 열린 크롬 창에서 아카라이브에 직접 로그인 (로그인 유지 선택)
3. 창을 닫음 → **세션 확인** 버튼으로 로그인 상태 확인
4. 채널에서 **지금 실행**

프로그램은 아이디·비밀번호를 저장하거나 대신 입력하지 않습니다. 세션(쿠키)은 `data/browser_profile`에 남아
이후 자동 실행에 계속 사용됩니다. 로그아웃·만료되면 실행이 `LOGIN_REQUIRED`로 중단되고 GUI 상태 페이지에 표시됩니다.

## 네이버 카페

채널 추가에서 사이트를 **네이버 카페**로 고르고 카페 주소(`https://cafe.naver.com/iroid`)나 URL 이름을 넣습니다. 특정 게시판만 원하면 메뉴 주소(`.../menus/3365`)를 넣습니다.

- 목록은 공개 카페면 로그인 없이 읽히지만, **본문은 네이버 로그인 세션이 필요**하고 회원 전용·등급 게시판은 해당 카페 가입·등급이 있어야 합니다. 권한이 없는 글은 `blocked`로 기록하고 재시도하지 않습니다(오류 페이지에서 권한이 생긴 뒤 다시 대기열에 넣을 수 있음).
- 로그인은 **브라우저 세션 → 네이버 카페 로그인 브라우저 열기**로 열린 일반 Chrome에서 직접 합니다(2단계 인증·새 기기 확인도 그 창에서). 프로그램은 자동 로그인이나 캡챠 우회를 하지 않습니다.
- 이미지는 `pstatic.net` CDN에서 크기 파라미터를 뗀 원본을 받습니다. 원본 API 응답(JSON)은 `data/raw/`에 그대로 보관됩니다.
- **요청 방식**: 네이버 브라우저 모드에서는 목록 API, 본문 페이지의 API 응답, 이미지 탭을 사용합니다. 회원 전용 본문 수집의 실제 동작은 별도 세션 검증이 필요합니다.
- **속도 상한**: 네이버는 요청 간 최소 3초 + 무작위 2초, 실행당 글 60개로 제한됩니다(전역 설정보다 보수적인 쪽 적용). 자동입력 방지 화면이 보이면 즉시 중단합니다.

## 동작 방식

| 단계 | 내용 |
|---|---|
| discover | 목록 `?p=N`을 읽어 신규 글 ID를 등록. 첫 실행은 `initial_pages`만, 이후에는 한 페이지의 글이 모두 아는 글이면 중단 |
| collect | 글마다 본문·메타·댓글·미디어 참조 파싱, 원본 HTML gzip 보관, 미디어 즉시 다운로드(서명 URL이 1시간 만료) |
| recheck | 최근 `recheck_days` 내 글의 수정/삭제 감지(이전 본문은 revision으로 보존), 만료된 미디어 URL 갱신 |
| backlog | 신규 목록 탐색과 별도 예약으로 기존 미수집 파일 처리. 공통 기본값은 15분 간격·10분 예산·글당 8개. 운영 설정의 `aiart`만 20분·16개 |

- 가져오기: `auto` 모드는 HTTP로 시작하고 451 / 로그인 필요 / Cloudflare 확인 화면을 만나면 브라우저(Playwright + Chrome)로 자동 전환하며 채널에 `브라우저 필요`를 기록합니다.
- 대기 제한: `media.backlog_time_budget_by_channel`과 `media.backlog_files_per_article_by_channel`에 채널 slug별 값을 지정할 수 있습니다. 원본 우선 저장 정책은 유지합니다. 기존 글 재확인에서 HTTP 451을 받으면 원문 접근 제한과 다음 재확인 시각을 기록합니다. 같은 브라우저 세션의 로그인 상태가 확인되면 해당 글을 건너뛰고 다음 글을 처리하며, 확인되지 않으면 실행을 중단합니다. 해당 응답은 다운로드 불가 확정이나 우회 대상으로 취급하지 않습니다.
- 실패 처리: 항목 실패는 지수 백오프로 재시도(기본 4회, 10분→최대 6시간), 초과 시 `error`로 남겨 GUI 오류 페이지에서 수동 재등록. 세션 수준 실패(로그인·차단·429·연속 네트워크 오류)는 실행을 중단하고 원인 코드를 남깁니다.
- 속도 제한: 페이지 요청 2초 + 지터, 미디어 0.4초.
- 저장: SQLite `data/arca.sqlite3`(WAL), 미디어 `data/media/{채널}/{글ID}/NN_해시.확장자`, 원본 `data/raw/…html.gz`, 로그 `data/logs/app.log`. 같은 파일(SHA-256 동일)은 하드링크로 중복 저장을 피합니다.
- 아카라이브 원본 정책: 원본 후보를 우선 받고 실패하면 제공본을 보관합니다. 403/404는 **원본 요청 실패**로 표시하며 영구 부재로 단정하지 않습니다. 파일 시그니처가 원본 형식과 다르면 변환본으로 보관합니다. 아카콘도 저장합니다. 디시 제공 이미지는 원본 여부를 미확인으로 유지합니다.
- 원본 서버 차단기: 원본 서버 연결 실패가 반복되면 원본 요청을 잠시 미루고 제공본을 받습니다. 기존 대기는 별도 작업이 처리하며, 처리 완료 시점은 전송 속도·신규 유입·접근 상태에 따라 달라집니다.
- 긴 파일: 요청당 기본 600초 전송 예산을 적용합니다. ETag/Last-Modified와 부분 파일 해시를 확인하고 HTTP Range가 유효할 때 이어받습니다. 지원하지 않는 서버는 새로 받습니다. 제한 확인은 청크 경계에서 하므로 읽기 타임아웃만큼 더 걸릴 수 있습니다.
- 중지: 청크·페이지·대기 경계에서 중지하며, 응답이 없는 요청은 설정된 읽기 타임아웃까지 기다릴 수 있습니다.
- 스케줄러: 서버 내부 스레드가 채널별 `interval_minutes`로 실행. 동시 실행은 OS 파일 잠금으로 방지.

## 북마크

- **미디어** 카드 또는 확대 화면에서 `☆ 이미지 북마크`를 누르면 그 이미지 하나를 북마크합니다. `☆ 글 북마크`를 누르면 해당 게시글을 북마크합니다. 영상도 같은 방식으로 저장합니다.
- **게시글** 목록과 본문 화면에서도 글을 북마크할 수 있습니다. 채워진 별 `★`를 다시 누르면 해제됩니다.
- **북마크** 탭은 썸네일 격자로 표시합니다. 2.1.1부터 글을 북마크하면 본문의 모든 이미지·미디어가 본문 순서대로 각각 표시됩니다. 이미지 북마크는 선택한 파일만 표시합니다. 이미지가 없는 글과 미수집 파일도 목록에 남습니다. 표시 항목 60개마다 페이지를 나눕니다.
- **필터**: 글/미디어, 이미지·GIF·동영상·아카콘·외부 미디어, 채널, 제목·본문·작성자 검색, 최근/오래된 북마크순. 글의 미디어 종류 필터는 해당 글 안에서도 조건에 맞는 파일만 표시합니다. 글 북마크를 해제해도 개별 북마크한 이미지는 남습니다.
- 북마크는 SQLite에 참조만 저장합니다. 파일을 추가로 복사하거나 다운로드하지 않으며, 해제해도 게시글·미디어는 삭제하지 않습니다. 재시작과 DB 백업·복원 후에도 유지됩니다.

## CLI

```bash
.venv/Scripts/python.exe -m arca_archive run --channel ailove          # 1회 수집
.venv/Scripts/python.exe -m arca_archive login                          # 로그인 창
.venv/Scripts/python.exe -m arca_archive check-session                  # 세션 상태
.venv/Scripts/python.exe -m arca_archive add-channel bluearchive --mode auto --interval 60
.venv/Scripts/python.exe -m arca_archive channels
.venv/Scripts/python.exe -m arca_archive stats
.venv/Scripts/python.exe -m arca_archive backup                         # DB 온라인 백업 → data/backups
.venv/Scripts/python.exe -m arca_archive export --channel ailove        # 글·미디어 경로·댓글 JSONL → data/exports
```

## 운영 도구

- **댓글 복구**: 디시 댓글의 마지막 확인 결과를 게시글과 오류 탭에 표시합니다. 잘못된 댓글 행이나 응답 실패가 있어도 이미 저장한 본문·정상 댓글은 유지합니다. 미완료 댓글은 재시도 가능 시각 이후 채널 수집에서 다시 확인하며, 한도 초과 항목은 `댓글 재시도 예약`으로 재개합니다. 댓글 수집이 켜진 활성 채널에서 처리합니다. 글 작성일이 오래되어도 재시도 대상에 포함됩니다.
- **실행 요약**: 글·미디어·댓글 저장 수, 실패, 다음 회차 이월을 구분합니다. 채널 미디어 미완료 수는 실행 종료 시 확정하며, 실행 중에는 `종료 후 집계`로 표시합니다. 미완료는 다운로드 불가능을 뜻하지 않습니다.
- **과거 글 더 가져오기**: 채널 수정 화면에서 페이지 수를 넣으면 다음 실행이 목록을 그 페이지까지 훑어 과거 글을 등록합니다(아는 글이 나와도 멈추지 않음).
- **채널별 일시중지/재개**: 상태·채널 화면 버튼. 자동 실행만 멈추고 수동 실행은 가능.
- **백업/내보내기**: 설정 화면의 버튼과 링크, 또는 위 CLI. 미디어·원본 폴더는 함께 복사해 보관.
- **자동 시작**: `autostart_on.bat`으로 Windows 로그인 시 서버가 최소화 창으로 시작되게 등록, `autostart_off.bat`으로 해제.
- **용량**: 상태 화면 하단에 미디어 용량과 DB 크기 표시. 실행 이벤트 로그는 `crawl.keep_events_days`(기본 30일) 뒤 자동 정리.
- **대기 진단**: `/backlog`에서 미수집 파일 수와 처리 조건을 확인하고 채널별 대기 작업을 실행합니다. 표시된 개수는 글 수가 아닙니다.
- **검색·열람**: 제목/본문/작성자/댓글, 사이트, 한국 시간 기준 작성일, 파일 상태로 검색합니다. 본문은 안전한 태그와 로컬 미디어로 표시합니다.
- **재시작**: 수동 대기열과 중단 작업을 DB에 남겨 복구합니다. 명시적인 중지 요청은 대기열을 비웁니다. `app.log`에 서버 시작/종료, `faults.log`에 Python 치명적 오류를 기록합니다. 강제 종료 원인이 항상 기록되는 것은 아닙니다.

## 디시인사이드 2.0

사이트에서 **디시인사이드 (마이너 갤러리)**를 선택하고 `https://gall.dcinside.com/mgallery/board/lists/?id=aichatting` 형태의 주소를 등록합니다. 현재 검증 대상은 사용자가 지정한 `aichatting`입니다.

- 공개 목록·본문·직접 이미지/영상 참조와 댓글 조회 API를 사용합니다. 글·댓글 번호는 채널별 내부 ID로 분리합니다.
- 댓글은 기본 최대 10페이지까지 조회하며, 오류나 페이지 한도로 덜 받은 경우 부분 완료로 기록합니다. 연도가 없는 댓글 시각은 정확한 연도를 추측해 저장하지 않습니다.
- 외부 영상 플레이어·디시콘/음성 댓글 파일·일반 갤러리·미니 갤러리는 실제 수집 검증 범위에 포함되지 않습니다. 외부 임베드는 링크로 남깁니다. 업로더 원본과 동일한 파일이라는 보장은 하지 않습니다.

## 파일 검사와 독립 백업

아래 명령은 프로젝트 폴더에서 실행합니다. 전체 백업 전에는 스케줄러를 끄고 수집이 멈췄는지 확인하세요. 기존 폴더에 덮어쓰지 않습니다.

```powershell
.\.venv\Scripts\python.exe scripts\archive_tools.py audit --output data\diagnostics\integrity.json
.\.venv\Scripts\python.exe scripts\archive_tools.py backup --output D:\your-backup
.\.venv\Scripts\python.exe scripts\archive_tools.py verify --bundle D:\your-backup --output data\diagnostics\backup-verify.json
.\.venv\Scripts\python.exe scripts\archive_tools.py restore --bundle D:\your-backup --output D:\your-restored-archive
```

전체 백업은 DB·저장 파일·원문 이력·설정·SHA-256 목록을 포함합니다. 로그인 프로필은 포함하지 않으며 복원본은 자동 수집을 끕니다. 기존 GUI의 ‘DB 백업’ 버튼은 파일 전체 백업이 아닙니다. 이번 복원 실증은 전체 DB와 표본 파일 10개를 포함한 제한 시험이며 전체 미디어 복사 시험은 아닙니다.

종료 코드: 0 success, 3 partial(일부 실패), 1 failed, 130 취소.

## 공개 배포본의 테스트 자료

이 공유 ZIP에서는 실제 웹페이지 내용이 포함된 테스트 fixture와 테스트 코드를 제외했습니다.

## 디렉터리

```
arca_archive/
  common.py  config.py  db.py  storage.py  service.py  cli.py  export.py
  sites/     base.py(어댑터 인터페이스)  arca.py  naver_cafe.py  dcinside.py
  parsers/   list_page.py  article_page.py  page_state.py (아카라이브 HTML 파서)
  fetch/     http_fetcher.py  browser_fetcher.py  ratelimit.py  base.py
  pipeline/  runner.py  discover.py  collect.py  media.py  recheck.py  retry.py
  web/       app.py  templates/  static/
config/settings.json   data/ (DB·미디어·원본·로그·브라우저 프로필)
```

## 알려진 제약

- 아카라이브 댓글은 게시글 페이지에 렌더링된 범위를 수집합니다. 디시 댓글은 별도 API 페이지를 조회합니다.
- 외부 임베드(유튜브 등)는 URL만 기록하고 내려받지 않습니다.
- GUI는 Figma 디자인 제공 전까지 기능 확인용 최소 구성입니다.
