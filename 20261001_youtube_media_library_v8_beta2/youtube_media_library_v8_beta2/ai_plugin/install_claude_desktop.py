"""Register this local MCP server in the installed Claude Desktop config."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
SERVER = HERE / "mcp_server.py"
PYTHON = HERE / ".venv" / "Scripts" / "python.exe"
NAME = "youtube-media-library"


def candidate_configs() -> list[Path]:
    local = Path(os.environ["LOCALAPPDATA"])
    roaming = Path(os.environ["APPDATA"])
    return [
        local / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming"
        / "Claude-3p" / "claude_desktop_config.json",
        roaming / "Claude" / "claude_desktop_config.json",
    ]


def main() -> None:
    if not PYTHON.is_file() or not SERVER.is_file():
        raise FileNotFoundError("MCP 서버 또는 전용 Python 환경이 없습니다.")
    config = next((path for path in candidate_configs() if path.is_file()), None)
    if config is None:
        raise FileNotFoundError("Claude Desktop 설정 파일을 찾지 못했습니다.")
    document = json.loads(config.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("Claude Desktop 설정 형식이 올바르지 않습니다.")
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Claude Desktop의 mcpServers 설정 형식이 올바르지 않습니다.")
    target = {"command": str(PYTHON), "args": [str(SERVER)]}
    if servers.get(NAME) == target:
        print(f"Already registered: {config}")
        return
    if NAME in servers:
        raise ValueError("같은 이름의 다른 Claude Desktop MCP 설정이 있습니다.")

    backup = config.with_name(config.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(config, backup)
    servers[NAME] = target
    data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=config.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temp, config)
    finally:
        temp.unlink(missing_ok=True)
    assert json.loads(config.read_text(encoding="utf-8"))["mcpServers"][NAME] == target
    print(f"Registered: {config}\nBackup: {backup}")


if __name__ == "__main__":
    main()
