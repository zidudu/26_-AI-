"""글·미디어·댓글 내보내기(JSONL). 다른 도구와 연동할 때 사용합니다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .db import Database


def iter_articles(db: Database, channel_id: int | None = None, include_body_html: bool = False) -> Iterator[dict]:
    conn = db.connect()
    if channel_id:
        rows = conn.execute("SELECT a.*, c.slug AS channel_slug, c.site AS site FROM articles a JOIN channels c ON c.id=a.channel_id WHERE a.channel_id=? ORDER BY a.id", (channel_id,))
    else:
        rows = conn.execute("SELECT a.*, c.slug AS channel_slug, c.site AS site FROM articles a JOIN channels c ON c.id=a.channel_id ORDER BY a.id")
    for r in rows:
        a = dict(r)
        record = {k: a.get(k) for k in ("id", "remote_id", "site", "channel_slug", "url", "title", "category", "author", "author_type", "is_notice",
                                        "created_at", "edited_at", "view_count", "like_count", "dislike_count", "comment_count",
                                        "state", "collected_at", "body_text", "revision_count")}
        record["badges"] = json.loads(a.get("badges") or "[]")
        if include_body_html:
            record["body_html"] = a.get("body_html")
        record["media"] = [
            {k: m.get(k) for k in ("seq", "kind", "state", "file_path", "file_size", "sha256", "content_type", "width", "height",
                                   "is_original", "source_key")}
            for m in db.list_media(a["id"])
        ]
        record["comments"] = [
            {k: c.get(k) for k in ("id", "parent_id", "author", "created_at", "body_text", "is_deleted")}
            for c in db.list_comments(a["id"])
        ]
        yield record


def export_articles_jsonl(db: Database, out: Path, channel_id: int | None = None, include_body_html: bool = False) -> int:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for record in iter_articles(db, channel_id, include_body_html):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count
