# arca-archive 설계 문서 (초기 설계 기록)

현재 정책 및 검증 결과는 [2.0.0 보고서](RELEASE_V2.0.0.md)를 우선합니다.

작성 기준일: 2026-09-18. 대상 채널(테스트): https://arca.live/b/ailove

## 1. 조사 결과 요약

| 항목 | 확인 내용 |
|---|---|
| 목록 페이지 | `GET /b/{slug}?p=N` 서버 렌더링 HTML. 행은 `a.vrow.column` (공지는 `.notice`, 광고는 외부 href). 열: `col-id`(채널 내 번호) / `col-title`(`.badges`, `.title`, 댓글수) / `col-author` / `col-time time[datetime]`(UTC ISO) / `col-view` / `col-rate`. 페이지네이션 `.pagination a.page-link`. 카테고리 `.board-category a[href*=category]`. |
| 익명 접근 | ailove는 `has_sensitive_media=1` 채널. 비로그인 HTTP는 목록에서 일반 글이 숨겨지고(공지만 노출) 게시글은 **HTTP 451**. 로그인 세션(크롬)에서는 정상 노출. 공개 채널(예: breaking, bluearchive)은 익명 HTTP로 목록·게시글 모두 200. |
| 게시글 페이지 | `.article-wrapper > .article-head`(`.title` + `.badge.category-badge`, `.member-info .user-info a[data-filter]`, `.article-info`의 추천/비추천/댓글/조회수/작성일 `time[datetime]`), `.article-link a`(정규 URL), `.article-body > .fr-view.article-content`(본문), `.article-comment #comment`(댓글, `.title-comment-count`). |
| 미디어 | 본문 `<img>`/`<video>`. CDN 호스트 `ac.arca.live`(구 `ac.namu.la`). URL에 `?expires=<unix>&key=<sig>` **서명이 붙고 약 1시간 내 만료**. 따라서 미디어는 게시글 수집 직후 바로 내려받고, 재시도 시 게시글을 다시 열어 새 URL을 받아야 한다. 변환본(`ac.arca.live`, png→webp·gif→mp4 변환)과 원본(`&type=orig`, `ac-o.arca.live` 또는 `ac.arca.live` 모두 동작)이 있으며, 일부 원본 후보는 403을 돌려주지만 영구 부재를 뜻하지는 않는다. 원본 요청은 CDN 캐시가 없어 느리고 간헐적으로 실패한다. 아카콘(이모티콘)은 `kind=emoticon`으로 함께 저장(아카콘만 있는 글의 내용 보존), 프로필 아바타는 제외. |
| 정렬/증분 | 등록순 목록은 글 ID 내림차순. 글 ID는 사이트 전역 증가값이므로 "이미 아는 ID"를 만나는 지점으로 증분 경계를 판단할 수 있다. |
| 차단 감지 | 451(법적/성인 제한), 403/429/503, Cloudflare challenge(`cf-mitigated` 헤더), 로그인 페이지 리다이렉트(`/u/login`), 삭제 글 안내를 상태 코드로 구분. |

## 2. 핵심 결정

- **언어/런타임**: Python 3.11 (`.venv`), 의존성은 `pyproject.toml`.
- **가져오기 계층 2종 + 자동 전환**
  - `HttpFetcher`(httpx): 공개 채널용. 빠르고 가볍다.
  - `BrowserFetcher`(Playwright + 시스템 Chrome 채널, 전용 프로필 `data/browser_profile`): 로그인이 필요한 채널용. 사용자가 GUI에서 "로그인 브라우저 열기"로 직접 로그인하며, 프로그램은 자격증명을 다루지 않는다. 미디어도 같은 브라우저 컨텍스트의 요청 API로 내려받아 쿠키·UA를 공유한다.
  - 채널별 `fetch_mode = auto | http | browser`. `auto`는 HTTP로 시작하되 451/로그인 필요/목록 숨김이 감지되면 브라우저로 전환하고 채널에 `requires_browser`를 기록한다.
  - 사용자의 일반 크롬 프로필에 원격 디버깅으로 붙는 방식은 최신 Chrome에서 기본 차단되고 사용자 세션을 흔들 수 있어 채택하지 않는다.
- **저장소**: SQLite(WAL) 단일 파일 `data/arca.sqlite3` + 미디어 파일 `data/media/{channel}/{article_id}/`. 원본 HTML은 `data/raw/{channel}/{article_id}/{fetched_at}.html.gz`로 보관(재파싱·검증 가능).
- **실행 구조**: 단일 크롤 워커(스레드) + 실행(run) 단위 상태 기계. 한 실행은 `discover → collect → media → recheck` 단계로 진행하고, 개별 항목 실패는 기록 후 계속, 세션 수준 실패(로그인 필요·차단·연속 네트워크 오류)는 실행을 중단하고 원인 코드를 남긴다. 중지 요청은 단계/항목 경계에서 확인한다.
- **재시도**: 항목별 `attempts`, `next_retry_at`(지수 백오프 10분→최대 6시간), `max_attempts`(기본 4) 초과 시 `error`(수동 확인). 미디어 URL 만료는 게시글 재열람으로 URL을 갱신해 재시도.
- **스케줄러**: 서버 프로세스 내부 스레드. 채널별 `interval_minutes`(기본 60)와 활성 여부. 별도로 `python -m arca_archive run` CLI가 있어 Windows 작업 스케줄러 등록도 가능.
- **속도 제한**: 페이지 요청 간 최소 1.5초 + 지터, 미디어 요청 간 0.3초, 429/503/challenge 감지 시 백오프 후 실행 중단.
- **GUI**: FastAPI + Jinja2 서버 렌더링, 최소한의 개발용 UI(표·폼·버튼). Figma 제공 전까지 장식 요소를 넣지 않는다.
- **결정적 코드 우선**: 파싱·상태 전이·재시도·검증은 모두 일반 코드와 테스트로 처리한다. AI/MCP는 V1에 포함하지 않는다.

## 3. 상태 기계

게시글(article.state):
`discovered → collected | blocked(login/451) | deleted | failed(재시도 대기) | error(수동)`
- `collected` 글도 `recheck` 대상이 되며 본문 해시가 바뀌면 `article_revisions`에 이전 본문을 남기고 `edited_at`을 갱신한다. 삭제 안내를 만나면 `deleted`로 바꾸되 기존 데이터는 보존한다.

미디어(media.state):
`pending → downloaded | failed(재시도) | expired(URL 갱신 필요) | skipped(외부/미지원) | error`

실행(run.status): `running → success | partial | failed | cancelled`, `code`에 중단 원인(`LOGIN_REQUIRED`, `RATE_LIMITED`, `BLOCKED_451`, `NETWORK`, `CANCELLED` …).

## 4. 디렉터리

```
arca_archive/
  common.py        시간(KST)·해시·원자적 파일 저장·오류 타입
  config.py        설정 로드/검증(pydantic) config/settings.json
  db.py            SQLite 스키마·마이그레이션·저장소 함수
  parsers/         list_page.py, article_page.py, page_state.py (순수 함수)
  fetch/           base.py(프로토콜), http_fetcher.py, browser_fetcher.py, ratelimit.py
  pipeline/        discover.py, collect.py, media.py, recheck.py, runner.py, retry.py
  storage.py       미디어/원본 경로 규칙
  scheduler.py     주기 실행
  web/             FastAPI 앱, API, 템플릿
  cli.py           run / serve / login / check
tests/             파서(고정 HTML), 상태 기계, DB, 파이프라인(가짜 fetcher), API
```

## 5. 참고 프로젝트에서 가져온 것 / 바꾼 것

- 가져옴: 원자적 JSON 저장, OS 파일 잠금 기반 RunLock, 실행별 `events`, 항목 상태 분류(보류/실패/재시도/수동), "실패는 기록하고 실행은 계속" 원칙, 로그인 등 세션 오류는 전체 중단, 자격증명 미저장·수동 로그인 프로필.
- 바꿈: 검색어 기반 발견 → 채널 목록 ID 기반 증분 발견. 실행별 폴더 JSON → SQLite 단일 이력 + 원본 HTML 보관. 배치 파일 CLI → 상주 서버 + HTML GUI + 내장 스케줄러. 브라우저 전용 → HTTP/브라우저 자동 전환. 미디어 수집 신규 추가.

## 6. 미디어 원본 정책 (V1.1)

- 다운로드 후보 순서: 원본(`data-originalurl`) → 원본 대안(변환본 URL + `type=orig`) → 변환본.
- 원본을 받으면 `is_original=1`. 원본이 실패해 변환본을 받으면 `is_original=0`으로 저장하고, 다음 실행의 재확인 단계에서 글을 다시 열어 새 서명 URL로 원본을 최대 3회 재시도한다(`media_upgraded`). 성공하면 파일을 교체하고 옛 변환본은 다른 항목이 참조하지 않을 때 삭제한다.
- 원본 후보가 403/404로 거부되면 현재 자동 재시도 정책에 따라 `original_unavailable=1`로 표시해 더 시도하지 않는다.
- 이 정책으로 실제 데이터에서 발견된 "png 원본인데 webp 저장 17건, gif 원본인데 mp4 저장 6건"이 파일 형식을 검증하고, 원본 요청이 실패하면 그 상태를 표시한다. 원본의 영구 부재는 확정하지 않는다.

## 7. 다중 사이트 구조 (V1.2)

- `sites/base.py`의 `Site` 인터페이스: 채널 입력 정규화, 목록/글 요청(html 또는 json), 응답 상태 분류, 글 파싱(`ArticleData`), 내부 ID 계산, 미디어 호스트, 로그인 주소.
- 내부 ID: 아카라이브는 사이트 전역 글 번호를 그대로 PK로 사용(기존 호환). 다른 사이트는 `channel_id × 10^12 + 원격 ID`로 만들어 충돌하지 않게 하고, `articles.remote_id`/`comments.remote_id`에 원격 ID를 함께 저장.
- 채널: `channels.site`(arca | naver_cafe), `site_channel_id`(네이버는 숫자 카페 ID), `category`(네이버는 메뉴 ID).
- fetcher에 `fetch_api()` 추가: HTTP는 httpx, 브라우저는 컨텍스트 요청 API(로그인 쿠키 공유)로 JSON API 호출. 401/로그인 필요 시 HTTP → 브라우저 자동 전환은 그대로 동작.
- 글 상태에 `blocked`(가입·등급 필요) 추가: 재시도하지 않고 기록만. 세션 로그인 상태는 사이트별로 저장.
- 네이버 카페 어댑터가 확인한 API/CDN 규칙은 `sites/naver_cafe.py` 상단 주석과 `docs/VALIDATION_V1.md`에 기록.

## 8. 요청 지문과 페이지 주도 수집 (V1.2.1)

측정(2026-09-19, 헤더 에코·TLS 지문 서비스):

| 방식 | TLS 지문 | HTTP | Sec-Fetch·Client Hints |
|---|---|---|---|
| httpx 직접 호출 | Python | 1.1 | 없음 |
| Playwright `context.request` | Node.js(브라우저 아님) | 1.1 | 없음 |
| 실제 페이지 로드 / 페이지 내 `fetch` | Chrome | 2 | 전부 있음 |

결정: 로그인 계정 쿠키를 쓰는 사이트(네이버)는 요청이 100% 브라우저에서 나가도록 한다.
- `Request.kind = page_capture`: 실제 글 페이지를 열고 `page.expect_response`로 SPA의 API 응답을 가로챈다. 가로채지 못하면 페이지 내용과 세션 쿠키 유무로 로그인 필요/가입 필요/삭제/캡챠를 판단한다.
- `Request.kind = json` + `origin`: 해당 사이트 페이지 위에서 페이지 내 `fetch(credentials: include)`로 호출한다(CORS 허용 확인). 실패 시에만 `context.request`로 대체하고 표시를 남긴다.
- `SitePolicy.in_page_media`: 미디어를 별도 탭 탐색으로 받아 응답 본문을 저장한다(사용자가 이미지를 새 탭에서 여는 것과 동일).
- UA: 실제 Chrome UA에서 `HeadlessChrome`만 `Chrome`으로 바꿔 쓰고 `data/browser_ua.json`에 캐시. Chrome 업데이트로 버전이 바뀌면 자동 재설정(클라이언트 힌트와 불일치 방지). HTTP 모드도 같은 UA 사용.
- 아카라이브는 HTTP 모드에서 여전히 httpx 지문이지만, 로그인 채널은 브라우저 모드로 전환되므로 페이지 로드가 사용된다. 미디어는 서명 URL만 검사하는 CDN이라 httpx 스트리밍을 유지.
