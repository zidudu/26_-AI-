# Claude용 등록 스킬

[`SKILL모음/`](../../SKILL모음/)에서 Claude에 쓸 만하다고 판단한 스킬을 골라 이 프로젝트의 Claude Code 스킬 폴더에 넣었습니다. 이 저장소를 Claude Code로 열면 자동으로 인식됩니다(프로젝트 스킬). 모든 프로젝트에서 쓰려면 폴더를 `~/.claude/skills/`로 복사하세요.

claude.ai에서 쓰려면 [`SKILL모음/claude-upload/`](../../SKILL모음/claude-upload/)의 ZIP을 **Settings > Features**에서 하나씩 업로드합니다. claude.ai·Claude Code·API의 스킬은 서로 동기화되지 않습니다([Anthropic 문서](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)).

| 스킬 | 구분 | 용도 | 필요 조건 |
| --- | --- | --- | --- |
| `evidence-gate` | 추천 | 외부 자료를 검증한 뒤 쓸 만한 후보만 추천 | 없음 |
| `evidence-driven-debugging` | 추천 | 가설 → 실험 → 확인 순서의 디버깅 | 없음 |
| `mermaid-workflow-diagram` | 추천 | 제공된 단계를 그대로 Mermaid 다이어그램으로 | 없음 |
| `visual-skills` | 추천 (Claude Code 전용) | PR·커밋 리캡(visual-recap) 등 HTML 시각 문서 6종 | Node.js 20+, npm, 인터넷(최초 1회 의존성·D2 설치) |
| `suno-v6-music-creation` | 추천 | Suno v6 음악 프롬프트 | 없음 |
| `novelai-v5-image-production` | 추천 | NovelAI V5 이미지 프롬프트 | 없음 |
| `github-deep-search` | 조건부 | 참고할 오픈소스 구현 찾기 | GitHub 접근 |
| `interview-me` | 조건부 | 요구가 모호할 때 되물어 목적 파악 | 없음 |
| monitoring-automation 11개 | 조건부 | 수집·분석·보고·발송 자동화 설계 | 일부 스크립트: Pillow, openpyxl / Office 작업은 Windows |

## 원본과 바꾼 점

- Codex 전용 메타데이터(`agents/openai.yaml`)와 아이콘은 넣지 않았습니다. 지침·참고자료·스크립트는 원본과 같습니다.
- `mermaid-workflow-diagram` 설명의 "ChatGPT에서"를 "채팅·GitHub에서"로 바꿨습니다.
- `visual-skills`는 원본 [scottyroges/visual-skills](https://github.com/scottyroges/visual-skills)(MIT)를 Codex용으로 감싼 것입니다. `visual-recap`만 따로 쓰려 해도 렌더러 런타임이 필요해 묶음째 넣었습니다. 설치 스크립트는 `npm ci --ignore-scripts`와 SHA-256이 검증된 D2 바이너리 다운로드만 하며, 런타임은 `git`·`gh`·`d2`만 호출합니다(2026-10-02 코드 확인).

## 확인한 것 (2026-10-02, Linux)

- 모든 스킬의 `name`·`description` 형식이 Claude 규칙(이름 64자 이하 소문자·하이픈, 설명 1024자 이하)에 맞습니다.
- `visual-skills`: `setup.py` 설치 성공, 이 저장소 커밋 `6b49944`로 `recap.html` 생성 성공.
- monitoring 스크립트 6개: `--help` 실행 성공(Pillow·openpyxl 설치 후).
- 실제 대화에서 각 스킬이 제때 호출되는지는 아직 시험하지 않았습니다.

## 함께 정리할 것

이미 계정에 설치된 **Suno v5.5**(`suno-pro-producer`)·**NovelAI V4.5**(`novelai-v45-prompt-engineer`) 스킬과 역할이 겹칩니다. v6·V5를 쓰신다면 claude.ai 설정에서 이전 스킬을 끄세요.
