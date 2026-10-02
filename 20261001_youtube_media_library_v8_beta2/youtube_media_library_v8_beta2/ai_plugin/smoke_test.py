"""Exercise the MCP protocol over stdio against the live local library."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


HERE = Path(__file__).resolve().parent


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "mcp_server.py")],
        cwd=str(HERE),
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = {tool.name for tool in tools}
            assert names == {"yme_search_library", "yme_get_item", "yme_read_text", "yme_list_jobs"}, names
            assert all(tool.annotations and tool.annotations.read_only_hint for tool in tools)

            found = await session.call_tool("yme_search_library", {"limit": 2})
            assert not found.is_error, found
            search = found.structured_content
            assert search and search["total"] >= 1 and search["items"]

            item_id = search["items"][0]["id"]
            detail = await session.call_tool("yme_get_item", {"item_id": item_id})
            assert not detail.is_error and detail.structured_content["id"] == item_id

            jobs = await session.call_tool("yme_list_jobs", {"limit": 2})
            assert not jobs.is_error and "jobs" in jobs.structured_content

            text_check = "unavailable"
            for item in search["items"]:
                detail = await session.call_tool("yme_get_item", {"item_id": item["id"]})
                if any(file["kind"] == "transcript" for file in detail.structured_content["files"]):
                    result = await session.call_tool("yme_read_text", {"item_id": item["id"], "limit": 120})
                    assert not result.is_error and result.structured_content["text"]
                    text_check = "ok"
                    break

            print(json.dumps({"tools": sorted(names), "library_total": search["total"],
                              "item_lookup": "ok", "recent_jobs": len(jobs.structured_content["jobs"]),
                              "transcript_read": text_check}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
