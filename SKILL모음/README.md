# Codex 스킬 모음

Codex에서 확인한 스킬 자료와 사용자가 올린 `skills.zip`의 압축 해제본을 한곳에서 찾아볼 수 있도록 정리한 보관 모음입니다. 스킬별 안내와 리소스를 원본 폴더 구조에 가깝게 보존했습니다. 이 저장소에 보관했다는 뜻은 Codex에 새로 설치하거나 활성화했다는 뜻은 아닙니다.

## 한눈에 보기

| 위치 | 내용 | 규모 |
|---|---|---:|
| [`monitoring-automation/`](monitoring-automation/) | 기존에 보관하던 모니터링·자동화 스킬 묶음. 기존 파일을 그대로 유지했습니다. | 11개 스킬 |
| [`codex-current/`](codex-current/) | 2026-10-02에 현재 세션의 스킬 카탈로그에서 수집한 스냅샷. 각 항목의 전체 폴더와 함께 복사했습니다. | 카탈로그 234개 항목, `SKILL.md` 파일 249개 |
| [`codex-export/`](codex-export/) | 이번 대화에서 받은 `skills.zip`을 풀어 둔 자료. `SKILL.md`가 있는 개별 스킬 폴더를 모두 보존했습니다. | 16개 스킬 폴더 |
| [`CATALOG.json`](CATALOG.json) | 현재 카탈로그 항목의 표시 이름, 설명, 원래 패키지 경로, 이 모음 안의 경로와 ZIP 스킬 폴더 목록 | JSON 색인 |
| [`CATALOG_SHA256SUMS.txt`](CATALOG_SHA256SUMS.txt) | `codex-current/`, `codex-export/`, `CATALOG.json` 파일의 무결성 확인용 SHA-256 목록 | 파일별 해시 |

`codex-current`의 항목 수와 실제 `SKILL.md` 수가 다른 까닭은 일부 상위 스킬에 하위 스킬이나 예제 스킬이 함께 들어 있기 때문입니다. 두 수치는 중복을 제거한 전체 고유 스킬 수를 뜻하지 않습니다.

## 폴더 안내

### `monitoring-automation/`

이번 정리 전부터 있던 모음입니다. 기존 README, `CONTENTS.json`, `SHA256SUMS.txt`, ZIP 파일과 11개 스킬 폴더를 그 위치에 남겨 두었습니다. 이 폴더의 체크섬은 기존 11개 배포본을 대상으로 하며, 새 스냅샷 체크섬과 별개입니다.

### `codex-current/`

현재 세션에서 조회된 스킬 카탈로그 234개 항목의 스냅샷입니다. 이름에 `패키지:` 접두어가 붙은 플러그인 스킬은 `플러그인/스킬이름/` 형태로 저장했습니다. 접두어가 없는 항목은 이름에 해당하는 폴더에 저장했습니다. 자세한 원래 이름과 패키지 URI, 저장 경로는 [`CATALOG.json`](CATALOG.json)의 `current_skills` 배열에서 찾을 수 있습니다.

이 목록은 당시 로드된 카탈로그의 스냅샷입니다. 개인이 직접 작성한 스킬만을 의미하지 않으며, 기본·시스템 스킬과 플러그인 제공 스킬도 포함합니다. 카탈로그가 갱신되면 이 스냅샷의 수와 현재 UI의 목록이 달라질 수 있습니다.

카탈로그의 플러그인별 항목 수는 다음과 같습니다. 플러그인 접두어가 없는 독립 항목 50개도 별도로 포함됩니다.

| 카탈로그 접두어 | 항목 수 |
|---|---:|
| `understand-anything` | 9 |
| `build-web-apps` | 6 |
| `build-web-data-visualization` | 1 |
| `cloud-environment` | 1 |
| `data-analytics` | 15 |
| `app-6a3c278c93ac8191b29768648d63a754` | 1 |
| `fal` | 15 |
| `figma` | 14 |
| `game-studio` | 9 |
| `google-drive` | 5 |
| `life-science-research` | 50 |
| `notion` | 4 |
| `openai-developers` | 4 |
| `pages` | 4 |
| `work-pets` | 3 |
| `plugin-management` | 1 |
| `product-design` | 5 |
| `remotion` | 12 |
| `sites` | 4 |
| `superpowers` | 15 |
| `write-like-me` | 1 |
| `defense-factory` | 1 |
| `demos` | 2 |
| `openai-library` | 1 |
| `template-creator` | 1 |
| **접두어가 없는 항목** | **50** |
| **합계** | **234** |

### `codex-export/`

업로드한 ZIP에서 16개 `SKILL.md` 폴더를 추출했습니다. 일반 스킬 11개와 `.system` 아래의 시스템 스킬 5개가 포함됩니다. 숨김 디렉터리 `.system`은 내용을 유지한 채 알아보기 쉬운 `system/` 이름으로 정리했습니다. ZIP에서 이름이 겹치는 스킬도 출처별 내용을 보존하도록 현재 카탈로그 스냅샷과 별도 경로에 두었습니다.

각 스킬의 안내는 해당 폴더의 `SKILL.md`에서 시작합니다. `references/`, `scripts/`, `assets/`, `tests/` 같은 하위 폴더가 있으면 해당 스킬의 보조 자료이므로 함께 확인하세요.

## 스킬 찾는 법

1. 먼저 [`CATALOG.json`](CATALOG.json)에서 표시 이름이나 설명을 검색합니다.
2. 항목의 `path` 값을 따라가 해당 스킬 폴더를 엽니다.
3. 사용 목적과 지침은 `SKILL.md`를 읽고, 필요한 경우 연결된 참고자료와 스크립트를 확인합니다.
4. ZIP 출처 스킬은 `codex-export/`에서 폴더 이름을 찾습니다. `system/` 안에는 ZIP의 `.system` 묶음이 있습니다.

## 보존·제외 기준

- 스킬의 `SKILL.md`와 함께 제공된 참고 문서, 스크립트, 테스트, 예시, 이미지 및 기타 지원 파일을 보존했습니다.
- Python 캐시(`__pycache__/`, `*.pyc`, `*.pyo`)는 실행에 필요하지 않은 생성물이라 제외했습니다.
- `CATALOG_SHA256SUMS.txt`는 `codex-current/`, `codex-export/`, `CATALOG.json`의 파일별 체크섬을 담습니다. 체크섬 파일 자신과 이 README는 자체 검증 목록에서 제외합니다.
- 기존 `monitoring-automation/`은 기존의 `SHA256SUMS.txt`와 `CONTENTS.json`으로 따로 관리합니다.

## 출처와 시점

- 현재 카탈로그 스냅샷: 2026-10-02 세션에서 확인
- ZIP 스킬 자료: 이번 대화에서 업로드한 `skills.zip`
- 기존 자동화 모음: 이 폴더에 이미 있던 자료

원본 스킬의 작성자, 이용 조건, 외부 도구 요구사항은 각각의 스킬 파일과 원래 배포처를 확인하세요. 이 모음은 파일을 찾아보기 위한 정리본이며, 개별 스킬의 호환성이나 실행 동작을 새로 검증한 인증 자료는 아닙니다.
