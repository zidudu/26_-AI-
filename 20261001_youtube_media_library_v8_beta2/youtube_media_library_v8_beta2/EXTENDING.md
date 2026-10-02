# 확장 지점

이 프로그램은 파일 수집·색인, 작업 처리, HTTP API, 화면, AI 연결이 분리되어 있습니다. 기존 결과 파일과 SQLite 데이터를 유지하면서 다음 지점에서 기능을 추가합니다.

| 기능 | 수정 위치 | 함께 확인할 곳 |
| --- | --- | --- |
| 새 결과 파일 종류 | `yme/library.py`의 `classify()`, `mime()` | `static/app.js`의 `FILE_KINDS`, 파일 목록 및 다운로드 |
| 새 수집·변환 작업 | `yme/engine.py` 또는 `yme/performance.py`, `yme/jobs.py` | `app.py`의 입력 검증·API, 작업 상태 UI |
| 화면 패널 | `static/index.html` | `static/styles.css`, `static/app.js`의 `bindPanelControls()` |
| AI 도구 | `ai_plugin/mcp_server.py` | `ai_plugin/README.md`, `ai_plugin/smoke_test.py` |

새 파일 종류를 추가할 때는 먼저 실제 출력 파일의 접미사와 형식을 명확히 정의하고, `classify()`에 넣으세요. `files` 테이블에 등록되면 `/api/items/{id}`와 `/media/{file_id}` 경로를 이용할 수 있습니다. 화면의 `FILE_KINDS`에 표시명, 순서, 원격 관리 연결의 다운로드 제한 여부를 넣으세요. 브라우저에서 재생할 형식이면 `renderMedia()`의 소스 선택도 따로 검토해야 합니다. 확장자가 같아도 코덱이 브라우저에서 지원되지 않을 수 있습니다.

새 작업은 먼저 서버에서 입력 범위와 대상 파일을 검증하고 작업 대기열에 넣으세요. 원본 파일은 덮어쓰지 않고 별도 결과물을 만들며, 완료 후 색인을 갱신하세요. 작업이 실패하거나 취소된 경우 원본과 기존 유효 결과물은 유지되어야 합니다.

각 화면 패널은 버튼의 `id="toggle-NAME"`, 본문의 `id="NAME-body"`, `aria-controls`를 맞춘 뒤 `bindPanelControls()`의 `panels` 배열에 등록하면 접기 상태가 브라우저에 저장됩니다. 새 화면 요소를 추가하면 CSS의 `1250px` 및 `650px` 경계와 세로 화면을 함께 확인하세요. 정적 파일을 바꾼 뒤에는 `index.html`의 CSS/JS `?v=` 값을 올려 기존 브라우저 캐시를 갱신하세요.

기본 점검 명령:

```powershell
$env:PYTHONUTF8='1'
& '.\.venv\Scripts\python.exe' -m pytest -q
node --check static\app.js
```

AI 연결은 로컬 라이브러리를 읽는 별도 MCP 서버이며, 웹 서버에 도구를 직접 추가하지 않습니다. 쓰기 기능을 AI 도구에 추가할 경우 인증·권한·사용자 확인 흐름을 별도로 설계해야 합니다.
