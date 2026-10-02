---
name: chatgpt-local-mcp
description: "Windows의 로컬 프로그램·저장 자료를 ChatGPT MCP 플러그인으로 연결하거나 연결 장애를 해결할 때 사용합니다. OpenAI Secure MCP Tunnel, 프로젝트별 프로필, 숨김 자동 실행과 실제 호출 검증을 다룹니다. 로컬 Codex·Claude 등록만 요청한 경우에는 해당 클라이언트 설정을 사용합니다."
---

# ChatGPT 로컬 MCP 연결

로컬 MCP를 공식 Secure MCP Tunnel로 ChatGPT에 연결하고 실제 자료 조회까지 확인한다. 한국어 존댓말로 간결하게 진행 상황을 알린다. 이 스킬을 만드는 요청 자체는 다른 프로젝트의 연결이나 데이터 접근을 승인하지 않는다.

## 시작할 때

1. 사용자가 지정한 실제 프로젝트 폴더, 적용되는 AGENTS.md, 기존 MCP·실행 환경·계정 설정을 확인한다. 실행 중인 웹 서버나 크롤러를 먼저 중지하지 않는다.
2. 기존 MCP가 있으면 재사용한다. HTTP 웹 페이지가 열린다는 사실은 MCP 지원의 증거가 아니다. stdio MCP는 `initialize` → `tools/list` → 필요한 `tools/call`을 실제 프로토콜로 검사한다. HTTP MCP는 해당 전송 방식으로 검사한다.
3. MCP가 없으면 프로젝트에서 승인된 데이터 범위와 필요한 도구를 정하고 어댑터를 만든다. 조회 연동은 검색·항목 조회·본문 조회를 우선한다. 수집·삭제·설정 변경 도구는 사용자가 요구한 경우에만 추가한다.
4. 현재 OpenAI 공식 문서와 설치된 `tunnel-client help quickstart`를 확인한다. CLI 옵션과 ChatGPT 메뉴 이름은 바뀔 수 있으므로 아래 기록을 고정된 UI 규칙으로 취급하지 않는다.

## 자격 증명과 프로젝트 분리

- 키는 존재 여부만 확인한다. 키 값, 전체 환경 변수, `.env` 파일 내용, 인증 헤더를 출력하지 않는다. OpenAI API Key 스킬이 제공되면 그 자격 증명 절차를 따른다. 현재 대화에서 이미 받은 키 사용 승인은 존중한다.
- 프로필에는 `env:OPENAI_API_KEY` 같은 참조를 쓴다. 프로세스 환경에 없고 사용자 환경에 있으면 로컬 프로세스에만 읽어 사용한다. 키를 소스·실행 인자·스킬·공유 ZIP에 넣지 않는다.
- 프로젝트마다 **서로 다른 alias, profile, 터널, ChatGPT 플러그인 이름**을 정한다. 예: `youtube-media-library`, `arca-archive`. 다른 프로젝트의 터널 ID·프로필·자동 실행 항목을 복사하거나 덮어쓰지 않는다.
- 설정과 로그는 프로젝트의 무시되는 폴더에 저장한다. 배포 스크립트가 재귀적으로 파일을 수집하면 `.gitignore` 외에 ZIP의 제외 규칙도 확인한다.

## 연결 절차

Windows 경로·CLI·자동 실행 작업을 시작할 때 [references/windows-tunnel.md](references/windows-tunnel.md)를 읽는다.

1. 공식 릴리스에서 대상 CPU용 `tunnel-client`를 설치하고 제공되는 체크섬을 대조한다. 정상적인 기존 설치는 재사용한다.
2. Platform 터널 설정에서 기존 대상 프로젝트의 터널을 확인하거나 새 터널을 만든다. 소유 Platform 조직과 **사용할 ChatGPT 작업 공간**을 연결한다. 조직 연결만으로 해당 ChatGPT 작업 공간에 보인다고 가정하지 않는다.
3. stdio 명령 또는 실제 HTTP MCP 주소로 프로필을 설정하고 `doctor --explain`을 확인한다. stdio에 대한 network probe SKIP은 실제 stdio 성공 검사를 대신하지 않는다.
4. 장기 실행에는 공식 `runtimes connect` 관리 기능을 우선한다. 전용 alias의 `process_running`, `healthy`, `ready`가 모두 true인지 확인한다. 로컬 관리 UI도 loopback으로 유지한다.
5. ChatGPT 플러그인 화면에서 MCP 앱 생성 → 터널 연결을 선택하고 실제 터널 ID를 지정한다. 서버가 OAuth를 구현하지 않는 읽기 전용 stdio라면 앱 인증은 '인증 없음'으로 선택한다. 이는 OpenAI 터널 인증을 제거하는 설정이 아니다. OAuth가 있는 서버의 인증을 끄지 않는다.
6. 생성 후 표시되는 앱 연결 단계까지 완료한다. '연결됨'과 **실제로 발견된 도구 이름·개수**를 확인한다.
7. 테스트 대화에서 승인된 작은 읽기 호출 한 번을 실행하고 로컬 조회 결과와 대조한다. 사용자 데이터나 인증을 다른 대상에 추가로 전달해야 하면 기존 승인 범위를 확인한다. UI를 다룬다면 현재 환경의 컴퓨터 사용 규칙을 따른다.
8. 자동 실행을 사용자가 원하면 로그인 후 숨김 실행을 등록한다. 키를 포함하지 않는 VBS/작업 항목과 시작·상태·중지 도우미를 제공한다. 기존 자동 실행 항목은 보존한다. 이 프로젝트의 터널만 종료한 뒤 자동 실행 경로로 다시 시작해 검사한다.

## 아카라이브 프로젝트에 적용

- 먼저 현재 폴더와 설정을 확인한다. `D:/arca-archive`는 알려진 후보 경로이며, 버전·포트·자료 수는 매번 확인한다.
- 기존 어댑터가 없으면 SQLite 스키마와 검색 코드를 읽고 게시글 검색·본문·댓글 조회부터 만든다. SQLite는 `mode=ro`와 `PRAGMA query_only=ON`으로 연다. SQL에는 파라미터 바인딩과 결과 길이·개수 제한을 적용한다.
- 세션 쿠키, 로그인 정보, 원본 설정 파일은 도구 결과에 포함하지 않는다. 파일 조회가 필요하면 허용된 자료 폴더를 기준으로 경로 탈출·심볼릭 링크·크기를 검사한다.
- 자료 검색 연동을 위해 크롤러를 시작하거나 대량 수집하지 않는다. 기존 웹 서버의 로그인·공개 범위·포트도 그대로 보존한다.
- 위 항목은 어댑터 설계 기준이다. 현재 아카라이브에 MCP가 구현되었다거나 ChatGPT에 연결되었다고 주장하지 않는다.

## 진단과 완료 기준

- ChatGPT에 터널이 없으면 작업 공간 연결과 Tunnels Read + Use를 확인한다. 생성·편집에는 Manage 권한이 별도로 필요하다. 로그인·권한 부족·CAPTCHA 등 실제 차단점이 있으면 필요한 사용자 단계만 전달한다.
- `healthy=true`만으로 원격 호출 성공을 주장하지 않는다. 도구가 비어 있으면 stdio 종료·stdout 오염·등록 방식·발견 로그를 점검한다. stdio의 일반 로그는 stderr로 보낸다.
- 수정 후 반복 검사는 남은 원인을 확인하는 데 필요한 범위로 제한한다. 다른 앱·사용자 프로필·API 키로 임의 전환하지 않는다. 공개 HTTPS 터널로 바꾸는 경우에는 노출 범위와 인증을 다시 검토한다.
- 최종 보고에 연결된 앱 링크, 실제 호출 결과, 자동 실행 설정 및 테스트 여부, PC 전원·인터넷 필요 조건을 짧게 적는다. 재부팅하지 않았다면 재부팅 검증을 했다고 쓰지 않는다.
- 웹 변경 결과는 사용자에게 검증 가능한 화면을 저장해 보여 준다. 개인 화면을 공개 GitHub에 자동 업로드하지 않는다.

## 공식 출처

- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [공식 tunnel-client 릴리스](https://github.com/openai/tunnel-client/releases/latest)
- [Platform 터널 설정](https://platform.openai.com/settings/organization/tunnels)
- [ChatGPT 플러그인](https://chatgpt.com/plugins)
