"""Read-only access to the existing YouTube Media Library database."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]


class LibraryBackend:
    def __init__(self, root: Path = ROOT):
        settings_path = root / "config" / "server.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        self.db = Path(settings["data_dir"]).expanduser().resolve() / "library.sqlite3"
        if not self.db.is_file():
            raise FileNotFoundError(f"라이브러리 DB를 찾지 못했습니다: {self.db}")

    @contextmanager
    def connect(self):
        path = quote(self.db.as_posix(), safe="/:")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            yield connection
        finally:
            connection.close()

    def search(self, query: str = "", limit: int = 20, offset: int = 0) -> dict:
        query = query.strip()[:200]
        limit = max(1, min(int(limit), 50))
        offset = max(0, min(int(offset), 100000))
        where, args = "", []
        if query:
            needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where = " WHERE title LIKE ? ESCAPE '\\' OR channel LIKE ? ESCAPE '\\' OR search_text LIKE ? ESCAPE '\\'"
            args = [needle, needle, needle]
        with self.connect() as connection:
            total = connection.execute("SELECT count(*) FROM items" + where, args).fetchone()[0]
            rows = connection.execute(
                "SELECT id,title,channel,source_url,added_at,favorite FROM items" + where
                + " ORDER BY added_at DESC,id LIMIT ? OFFSET ?", args + [limit, offset]
            ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "total": total, "offset": offset,
                "has_more": offset + len(items) < total}

    def get_item(self, item_id: str) -> dict:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id,title,channel,source_url,added_at,favorite,metadata FROM items WHERE id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ValueError("해당 자료가 라이브러리에 없습니다.")
            files = [dict(file) for file in connection.execute(
                "SELECT id,kind,name,size,mime FROM files WHERE item_id=? ORDER BY kind,name",
                (item_id,),
            )]
            tags = [tag[0] for tag in connection.execute(
                "SELECT t.name FROM tags t JOIN item_tags it ON t.id=it.tag_id "
                "WHERE it.item_id=? ORDER BY t.name", (item_id,),
            )]
        result = dict(row)
        metadata = json.loads(result.pop("metadata") or "{}")
        result["description"] = str(metadata.get("description") or "")[:4000]
        result["duration_seconds"] = metadata.get("duration_seconds")
        result["tags"] = tags
        result["files"] = files
        return result

    def read_text(self, item_id: str, kind: str = "transcript",
                  offset: int = 0, limit: int = 5000) -> dict:
        if kind not in {"transcript", "timestamp"}:
            raise ValueError("kind는 transcript 또는 timestamp여야 합니다.")
        offset = max(0, min(int(offset), 2_000_000))
        limit = max(1, min(int(limit), 10000))
        with self.connect() as connection:
            row = connection.execute(
                "SELECT f.path,f.name,i.folder FROM files f JOIN items i ON i.id=f.item_id "
                "WHERE f.item_id=? AND f.kind=? ORDER BY f.name LIMIT 1",
                (item_id, kind),
            ).fetchone()
        if row is None:
            raise ValueError("해당 자료에 요청한 텍스트 파일이 없습니다.")
        path = Path(row["path"])
        folder = Path(row["folder"])
        if (path.suffix.lower() != ".txt" or path.is_symlink()
                or path.parent.resolve() != folder.resolve()
                or not path.is_file() or path.stat().st_size > 2_000_000):
            raise ValueError("텍스트 파일을 읽을 수 없거나 크기 제한을 초과했습니다.")
        content = path.read_text(encoding="utf-8-sig", errors="replace")
        return {"item_id": item_id, "kind": kind, "text": content[offset:offset + limit],
                "offset": offset, "total_chars": len(content),
                "has_more": offset + limit < len(content)}

    def list_jobs(self, limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 50))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,kind,status,progress,stage,created_at,updated_at "
                "FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,),
            ).fetchall()
        return {"jobs": [dict(row) for row in rows]}
