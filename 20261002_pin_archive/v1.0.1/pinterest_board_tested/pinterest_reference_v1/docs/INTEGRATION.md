# 기존 크롤러 / API / MCP 연결

## 파일 기반 입력

```json
{
  "items": [{
    "file": "images/reference.png",
    "title": "파란색 로비 레퍼런스",
    "tags": ["blue", "lobby", "게임 UI"],
    "crawl_keyword": "anime game lobby",
    "description": "직접 확인한 내용",
    "category": "게임 UI",
    "author": "확인된 경우에만 입력",
    "source_url": "https://example.com/original",
    "pin_url": "",
    "license_note": "권리와 이용 조건은 별도 확인"
  }]
}
```

이미지 경로는 JSON 파일이 있는 폴더를 기준으로 합니다. `file` 대신 `image`도 지원합니다. 기존 파일 구조를 바꾸고 싶지 않으면 이미지 상위 폴더에 manifest를 만드세요. 폴더 밖으로 빠져나가는 상대 경로는 거부합니다.

`examples/import_from_crawler.py`는 한 장씩 업로드하므로 중간 실패 전의 성공 결과가 남습니다. 다시 실행하면 동일 이미지는 병합됩니다. URL 모드는 `image_url`을 사용하며 실제 다운로드는 앱 작업 큐가 수행합니다.

## HTTP API

기본 주소는 `http://127.0.0.1:8765`이며 사용 중이면 다음 빈 포트를 선택합니다. 아래 Python 예제는 `data/runtime.json`과 실제 서버 식별값을 확인해 현재 주소를 찾습니다. 별도 데이터 폴더를 쓰면 `discover_server(Path("D:/PinArchiveData"))`처럼 경로를 지정하세요. 외부 서비스에서 접근하도록 서버를 공개하지 마세요.

읽기 예제:

```python
import httpx
from app.runtime import discover_server

with httpx.Client(base_url=discover_server(), trust_env=False) as client:
    response = client.get("/api/pins", params={"q": "blue lobby", "limit": 6})
    response.raise_for_status()
    for image in response.json()["items"]:
        print(image["id"], image["title"], image["source_url"])
```

변경 요청에는 `/api/bootstrap`이 반환하는 `csrf` 값을 `X-PinArchive-CSRF` 헤더로 보냅니다. 프로세스를 재시작하면 바뀝니다. 이 토큰은 브라우저의 다른 사이트가 임의 변경 요청을 보내는 것을 막기 위한 것으로, 외부 사용자 인증 수단이 아닙니다.

```python
import json
from pathlib import Path
import httpx
from app.runtime import discover_server

image = Path("reference.png")
with httpx.Client(base_url=discover_server(), timeout=120, trust_env=False) as client:
    initial = client.get("/api/bootstrap")
    initial.raise_for_status()
    client.headers["X-PinArchive-CSRF"] = initial.json()["csrf"]
    metadata = [{"title": "파란색 로비", "tags": ["blue", "lobby"],
                 "crawl_keyword": "game UI", "source_url": "https://example.com/original"}]
    with image.open("rb") as f:
        result = client.post("/api/import", files=[("files", (image.name, f, "application/octet-stream"))],
                             data={"metadata": json.dumps(metadata, ensure_ascii=False)})
    result.raise_for_status()
    data = result.json()
    print("신규:", data["added"], "중복:", data["duplicates"], "실패:", data["errors"])
```

주요 API:

| 메서드 / 경로 | 동작 |
|---|---|
| GET `/api/health` | 버전과 서버 상태 |
| GET `/api/bootstrap` | 토큰·설정·통계·기능 상태. 키는 반환하지 않음 |
| GET `/api/pins` | q, mode, category, source, favorite, collection, untagged, offset, limit |
| GET/PATCH/DELETE `/api/pins/{id}` | 조회/수동 메타데이터 편집/완전 삭제 |
| GET `/media/{id}/image` | 원본 파일. `?download=true`로 다운로드 |
| GET `/media/{id}/thumbnail` | 이미지 미리보기 |
| POST `/api/import` | multipart files 및 선택형 metadata 배열 |
| POST `/api/import/urls` | `items`, `permission_confirmed`로 URL 작업 등록 |
| GET/POST `/api/collections` | 보드 목록/생성 |
| POST `/api/collections/{id}/pins` | `ids`, `add`로 보드 이미지 추가/제거 |
| POST `/api/collect` | Pinterest `target`, `limit`, `tags`, `collection_id`, `permission_confirmed` |
| GET `/api/jobs` | 최근 80개 작업 |
| POST `/api/jobs/{id}/cancel` | 중단 요청. 진행 중 요청/추론 종료 후 반영 |
| POST `/api/ai/analyze` | `ids`, `consent`; 비워 두면 미분석 최대 50개 |
| POST `/api/ai/rerank` | `query`, `ids` 최대 12개, `consent` |
| POST `/api/index` | `ids`, `consent`; 비워 두면 미색인 전체 |
| POST `/api/export` | 선택 `ids`의 원본·JSON ZIP |

전체 입력 스키마는 실행 중인 서버의 `/openapi.json`에서 읽을 수 있습니다. 외부 CDN에 의존하는 Swagger 화면은 제공하지 않습니다.

## 로컬 MCP

`05_mcp_config.bat` 출력은 실제 설치 경로를 사용합니다. 서버 포트가 바뀌어도 MCP가 실행 시 주소를 자동 발견합니다. 앱을 옮기면 설정을 다시 생성하세요. 별도 데이터 폴더는 `05_mcp_config.bat --data-dir "D:\\PinArchiveData"`로 지정합니다. 클라이언트마다 등록 설정 형식이 다를 수 있으므로 해당 클라이언트의 stdio MCP 설정에 command와 args를 옮깁니다.

크롤러 예제도 현재 서버를 자동 발견합니다. `--server http://127.0.0.1:8766`으로 주소를 직접 지정하거나 `--data-dir`로 다른 데이터 폴더를 지정할 수 있습니다. 상태 파일이 없거나 이전 서버를 가리키면 다른 포트에 임의 연결하지 않고 오류를 표시합니다.

도구는 5개이며 전부 읽기 전용입니다. `get_image`는 원본 크기 정보와 함께 최대 700×1000 WebP 썸네일을 MCP image content로 반환합니다. AI에게 파일명을 추측하게 하지 말고 검색 결과의 32자리 ID를 넘겨 주세요.

서버가 꺼져 있거나 이미지가 삭제되면 도구는 오류를 반환합니다. 텍스트만 반환하고 이미지를 본 것처럼 처리하지 않습니다. stdio 프로토콜은 2025-06-18, 2025-03-26, 2024-11-05 버전을 협상하며, 이 버전에는 원격 HTTP MCP 전송이 없습니다.
