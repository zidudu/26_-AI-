"""Local read-only MCP bridge for the YouTube Media Library."""

from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server import MCPServer

from library_backend import LibraryBackend


server = MCPServer(
    "youtube-media-library",
    title="YouTube Media Library",
    description="이 PC에 저장된 YouTube Media Extractor 라이브러리를 읽기 전용으로 검색합니다.",
    instructions=(
        "자료를 찾을 때 검색 도구로 ID를 얻고, 항목 조회와 자막 읽기 도구를 사용하세요. "
        "자막이 길면 offset을 늘려 이어 읽으세요. 이 서버는 수집·다운로드·파일 수정 기능을 제공하지 않습니다."
    ),
)

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=False)


@server.tool(
    name="yme_search_library",
    description="저장된 자료를 제목·채널·자막 내용으로 검색합니다. 빈 검색어는 최신 자료를 나열합니다.",
    annotations=READ_ONLY,
    structured_output=True,
)
def search_library(query: str = "", limit: int = 20, offset: int = 0) -> dict[str, Any]:
    return LibraryBackend().search(query, limit, offset)


@server.tool(
    name="yme_get_item",
    description="검색 결과의 ID로 자료의 제목, 출처, 설명, 태그, 이용 가능한 파일 종류를 조회합니다.",
    annotations=READ_ONLY,
    structured_output=True,
)
def get_item(item_id: str) -> dict[str, Any]:
    return LibraryBackend().get_item(item_id)


@server.tool(
    name="yme_read_text",
    description="자료의 자막(transcript) 또는 타임스탬프(timestamp)를 일부 읽습니다. 긴 텍스트는 offset으로 페이지를 넘깁니다.",
    annotations=READ_ONLY,
    structured_output=True,
)
def read_text(item_id: str, kind: str = "transcript", offset: int = 0,
              limit: int = 5000) -> dict[str, Any]:
    return LibraryBackend().read_text(item_id, kind, offset, limit)


@server.tool(
    name="yme_list_jobs",
    description="최근 수집 작업의 상태와 진행률을 조회합니다.",
    annotations=READ_ONLY,
    structured_output=True,
)
def list_jobs(limit: int = 20) -> dict[str, Any]:
    return LibraryBackend().list_jobs(limit)


if __name__ == "__main__":
    server.run(transport="stdio")
