# Windows 터널 실행: 재사용 시 확인할 사항

2026-10-02에 Windows + 공식 tunnel-client v0.0.15 + Python stdio MCP로 실행·ChatGPT 검색·종료 후 숨김 재시작을 검증했다. 설치할 버전과 옵션은 현재 공식 릴리스와 로컬 도움말로 확인한다. 이전 프로젝트의 개인 ID·키·계정 정보는 이 문서에 저장하지 않는다.

## 실행 명령과 검증

`--mcp-command`는 클라이언트가 명령 문자열을 파싱한다. Windows 백슬래시를 그대로 전달했을 때 경로의 백슬래시가 사라져 preflight가 실패했다. **슬래시 경로와 각 실행 인자 따옴표**를 사용한다.

```powershell
$McpExe = 'D:/your-project/tools/tunnel-client/tunnel-client.exe'
$McpProfileDir = 'D:/your-project/config/mcp-tunnel'
$McpAlias = 'your-project'
$McpCommand = '"D:/your-project/ai_plugin/.venv/Scripts/python.exe" "D:/your-project/ai_plugin/mcp_server.py"'
# $McpTunnelId는 실제 생성/조회한 비밀이 아닌 터널 ID.
& $McpExe init --sample sample_mcp_stdio_local --profile $McpAlias --profile-dir $McpProfileDir --tunnel-id $McpTunnelId --mcp-command $McpCommand --control-plane-api-key-ref env:OPENAI_API_KEY --health-listen-addr 127.0.0.1:0
& $McpExe doctor --profile $McpAlias --profile-dir $McpProfileDir --explain
```

각 명령의 종료 코드와 결과를 확인한 뒤 다음 작업을 한다. 키 사용이 승인된 경우에만 다음처럼 값을 출력하지 않고 사용자 환경을 읽을 수 있다.

```powershell
if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
    $env:OPENAI_API_KEY = [Environment]::GetEnvironmentVariable('OPENAI_API_KEY', 'User')
}
```

관리 실행에는 `runtimes connect --alias ... --profile ... --profile-dir ... --tunnel-id ... --mcp-command ... --runtime-api-key env:OPENAI_API_KEY --json`을 사용한다. **alias가 이미 있어도 connect에는 대상 명령 또는 서버 URL이 필요**했다. HTTP MCP는 `--mcp-server-url`을 쓰고 stdio 명령과 동시에 전달하지 않는다.

`runtimes connect`는 프로필을 다시 쓸 수 있으며 `.yaml` 파일에 JSON을 기록하기도 한다. 개인 프로필 파서가 필요하면 JSON부터 검사하고 YAML은 YAML 파서로 처리한다. 접미사만 보고 형식을 가정하지 않는다.

관리 모드의 health 포트는 매번 달라질 수 있다. 현재 `runtimes status <alias> --json`의 `ui_url` 또는 health URL 파일을 사용한다. 요청 전 `process_running`, `healthy`, `ready`를 확인한다. 전체 JSON에는 로그 꼬리가 포함될 수 있으므로 사용자 출력과 상태 파일에는 필요한 필드만 남긴다.

## 재사용 가능한 도우미

[../scripts/manage-tunnel.ps1](../scripts/manage-tunnel.ps1)은 별도 JSON 설정을 받아 connect/status/stop을 실행한다. 기본 Mode는 **status**다. 원격 터널·앱 생성, API 키 생성, 자동 실행 등록은 하지 않는다. connect를 호출하기 전에 현재 대화에서 키·프로젝트 연결 권한을 확보한다.

예시 설정을 프로젝트의 무시되는 `config/chatgpt-mcp.json`에 저장한다. 아래 값은 설명용이며 실제 경로와 생성한 ID로 바꾼다.

```json
{
  "alias": "arca-archive",
  "tunnel_id": "tunnel_REPLACE_WITH_CREATED_ID",
  "tunnel_client_path": "D:/your-project/tools/tunnel-client/tunnel-client.exe",
  "profile_dir": "D:/your-project/config/mcp-tunnel",
  "api_key_env": "OPENAI_API_KEY",
  "mcp_command": "\"D:/your-project/ai_plugin/.venv/Scripts/python.exe\" \"D:/your-project/ai_plugin/mcp_server.py\""
}
```

HTTP 설정이라면 `mcp_command`를 제거하고 `mcp_server_url`을 지정한다. 두 대상은 동시에 지정할 수 없다.

```powershell
& 'C:/Users/dlwlr/.codex/skills/chatgpt-local-mcp/scripts/manage-tunnel.ps1' -SettingsPath 'D:/your-project/config/chatgpt-mcp.json' -Mode status
```

도우미는 기존 alias의 프로필 폴더·터널 ID가 설정과 다르면 connect/stop을 거절한다. 다른 프로젝트의 런타임을 바꾸기 전에 새 alias를 사용하거나 기존 설정을 직접 확인한다. 출력에는 key 참조만 허용하며 키 값은 저장하지 않는다.

## 로그인 후 숨김 실행

자동 실행이 승인된 경우 프로젝트에 전용 시작 도우미를 둔다. 사용자 환경의 키 참조, 명령/프로필을 읽고 `runtimes connect`를 실행하도록 만든다. 이미 관리 중이면 그 런타임을 사용한다.

Windows Startup의 프로젝트별 VBS는 `WScript.Shell.Run`의 window style `0`으로 PowerShell 도우미를 숨겨 실행할 수 있다. PowerShell에 `-NoProfile -WindowStyle Hidden -File`을 넘기고 공백이 있는 경로를 따옴표로 감싼다. 프로세스 단위 ExecutionPolicy 옵션은 현장 정책상 필요하고 허용될 때만 사용한다. 키를 VBS·BAT에 직접 쓰지 않는다.

실행이 성공한 뒤 자동 실행 경로를 테스트한다. 테스트할 alias만 종료하고 VBS를 실행한 다음 상태 세 필드가 true인지 확인한다. 로그인 직후 네트워크 준비가 필요한 경우 숨김 시작 도우미에서 제한된 재시도(예: 20초 간격, 최대 4회)를 둘 수 있다. 임의의 모든 프로세스를 종료하거나 기존 웹 서버를 재시작하지 않는다.

## ChatGPT UI에서 확인할 것

실제 확인한 메뉴는 플러그인 → 추가 → MCP 앱 만들기 → 연결 '터널' → 인증 → 만들기 → 앱 연결이었다. 현재 화면을 읽어 단계와 메뉴를 조정한다.

- 로컬 읽기 전용 stdio MCP가 OAuth를 제공하지 않으면 '인증 없음'을 사용했다. 터널의 OpenAI API 키 인증은 계속 적용된다.
- 생성 후 별도 앱 연결 버튼까지 눌러야 '연결됨'이 표시됐다.
- 연결된 앱 상세에서 읽기 도구 개수와 실제 도구 이름을 확인할 수 있었다.
- '채팅에서 사용해 보기'로 앱이 선택된 대화를 열어 소량 검색을 했다. 예: 빈 검색어 + limit 1로 전체 개수와 최신 제목을 로컬 결과와 대조한다.

아카라이브 재사용 시에는 웹 서버를 MCP URL로 등록하지 말고 먼저 어댑터 존재와 프로토콜을 확인한다. 본문 검색을 위해 대량 크롤링이나 로그인 쿠키 공개가 필요하지 않다.
