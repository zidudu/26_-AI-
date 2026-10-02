# AI 연결 (MCP)

이 폴더는 YouTube Media Extractor의 **로컬 라이브러리를 읽는** MCP 서버입니다. 별도 Python 환경을 사용하므로 실행 중인 웹 서버의 패키지는 바꾸지 않습니다.

## 제공 도구

| 도구 | 기능 |
| --- | --- |
| `yme_search_library` | 제목, 채널, 자막 내용을 검색하고 최신 자료를 나열합니다. |
| `yme_get_item` | ID로 자료의 설명, 태그, 파일 종류를 조회합니다. |
| `yme_read_text` | 자막 또는 타임스탬프를 나누어 읽습니다. |
| `yme_list_jobs` | 최근 수집 작업의 상태를 조회합니다. |

DB는 SQLite `mode=ro`와 `query_only`로 열립니다. 새 다운로드를 시작하거나 파일을 수정하는 도구는 없습니다. AI에 전달된 자막과 메타데이터는 해당 AI 서비스의 데이터 처리 범위에 들어갈 수 있으므로 개인 자료를 검색할 때 이를 고려해 주세요.

## 새 PC에서 설치

프로그램의 `01_setup_beta.bat`으로 라이브러리를 먼저 설정하세요. 그다음 프로젝트 루트에서 PowerShell을 열어 별도 MCP 환경을 설치합니다.

```powershell
py -3 -m venv ai_plugin\.venv
& .\ai_plugin\.venv\Scripts\python.exe -m pip install -r .\ai_plugin\requirements.txt
```

Claude Desktop에 등록하려면 Claude Desktop 설정에서 개발자 설정 파일을 한 번 연 뒤 아래 도우미를 실행하고 앱을 다시 시작합니다. 기존 설정은 백업하며 다른 MCP 등록을 보존합니다.

```powershell
& .\ai_plugin\.venv\Scripts\python.exe .\ai_plugin\install_claude_desktop.py
```

Codex와 Claude Code에는 아래 명령의 Python 실행 파일과 서버 파일을 각각 MCP command/args로 등록하세요. 경로는 실제 압축 해제한 폴더에 맞춥니다.

## 서버 실행 명령 예시

서버 명령:

```text
D:\youtube_media_extractor_v8_beta2\ai_plugin\.venv\Scripts\python.exe D:\youtube_media_extractor_v8_beta2\ai_plugin\mcp_server.py
```

서버 이름은 `youtube-media-library`를 사용하면 됩니다. 등록 후 예: “내 YouTube Media Library에서 OOO 관련 영상을 찾고 자막을 요약해 줘.”

영상 자료를 하나 이상 저장한 뒤 연결을 점검합니다. `smoke_test.py`는 검색 결과가 하나 이상이고 실제 자막을 읽을 수 있는지 확인하는 개발용 검사입니다.

```powershell
& 'D:\youtube_media_extractor_v8_beta2\ai_plugin\.venv\Scripts\python.exe' 'D:\youtube_media_extractor_v8_beta2\ai_plugin\smoke_test.py'
claude mcp get youtube-media-library
codex mcp get youtube-media-library
```

Claude Desktop 설정 변경 전 파일은 같은 폴더에 `.bak-날짜` 이름으로 보관됩니다. 설정 방식은 [Claude 로컬 MCP 안내](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)를 참고하세요.

## ChatGPT 웹 연결

ChatGPT 웹에서 이 PC의 로컬 서버를 사용하려면 [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)을 사용할 수 있습니다. 터널은 위의 stdio 명령을 직접 실행할 수 있으므로 라이브러리를 인터넷에 공개할 필요가 없습니다. Platform의 터널 ID와 실행용 API 키, ChatGPT 개발자 모드 권한이 필요합니다. 이 계정 설정을 마친 뒤 `tunnel-client init`에서 `--mcp-command`에 위 명령을 지정하고, ChatGPT 플러그인 생성 화면에서 해당 터널을 선택합니다. 키는 채팅에 붙여 넣지 말고 로컬 환경에 보관하세요.

ChatGPT 웹용 터널 설정과 API 키는 배포 ZIP에 포함되지 않습니다. 공개 HTTPS MCP 주소를 사용하는 경우에는 별도 HTTP MCP 서버 구성이 필요합니다.
