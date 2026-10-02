"""SQLite 저장소. 스키마 생성/마이그레이션과 저장소 함수를 한 곳에 둡니다.

- 단일 파일 `data/arca.sqlite3`, WAL 모드.
- 모든 시각은 UTC ISO-8601 문자열.
- 스레드마다 자체 연결을 사용하고, 짧은 트랜잭션으로 처리합니다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from datetime import timedelta

from .common import now_iso, utcnow

SCHEMA_VERSION = 7

COMMENT_FETCH_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS comment_fetches (
      article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
      state TEXT NOT NULL CHECK(state IN ('pending','incomplete','error','complete')),
      expected_count INTEGER NOT NULL DEFAULT 0,
      saved_count INTEGER NOT NULL DEFAULT 0,
      attempts INTEGER NOT NULL DEFAULT 0,
      code TEXT,
      checked_at TEXT,
      next_retry_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_comment_fetches_retry ON comment_fetches(state, next_retry_at)",
)

BOOKMARK_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bookmarks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      article_id INTEGER UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
      media_id INTEGER UNIQUE REFERENCES media(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      CHECK ((article_id IS NOT NULL) != (media_id IS NOT NULL))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_created ON bookmarks(created_at, id)",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  site TEXT NOT NULL DEFAULT 'arca',
  site_channel_id TEXT,
  name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  fetch_mode TEXT NOT NULL DEFAULT 'auto',
  requires_browser INTEGER NOT NULL DEFAULT 0,
  interval_minutes INTEGER NOT NULL DEFAULT 60,
  initial_pages INTEGER NOT NULL DEFAULT 2,
  max_pages_per_run INTEGER NOT NULL DEFAULT 20,
  category TEXT,
  collect_media INTEGER NOT NULL DEFAULT 1,
  collect_comments INTEGER NOT NULL DEFAULT 1,
  include_notices INTEGER NOT NULL DEFAULT 1,
  recheck_days INTEGER NOT NULL DEFAULT 3,
  last_max_article_id INTEGER,
  last_run_id INTEGER,
  last_run_at TEXT,
  last_success_at TEXT,
  next_run_at TEXT,
  next_media_at TEXT,
  last_error_code TEXT,
  backfill_pages INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY,
  channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  remote_id TEXT,
  channel_local_no INTEGER,
  url TEXT NOT NULL,
  title TEXT,
  category TEXT,
  badges TEXT,
  author TEXT,
  author_type TEXT,
  is_notice INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  edited_at TEXT,
  view_count INTEGER,
  like_count INTEGER,
  dislike_count INTEGER,
  comment_count INTEGER,
  body_html TEXT,
  body_text TEXT,
  body_hash TEXT,
  state TEXT NOT NULL DEFAULT 'discovered',
  state_code TEXT,
  state_message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  discovered_at TEXT NOT NULL,
  collected_at TEXT,
  last_checked_at TEXT,
  deleted_at TEXT,
  raw_html_path TEXT,
  discovered_run_id INTEGER,
  collected_run_id INTEGER,
  media_total INTEGER NOT NULL DEFAULT 0,
  media_done INTEGER NOT NULL DEFAULT 0,
  revision_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_channel_state ON articles(channel_id, state);
CREATE INDEX IF NOT EXISTS idx_articles_channel_created ON articles(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_articles_retry ON articles(state, next_retry_at);

CREATE TABLE IF NOT EXISTS article_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  captured_at TEXT NOT NULL,
  title TEXT,
  body_html TEXT,
  body_text TEXT,
  body_hash TEXT,
  run_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_revisions_article ON article_revisions(article_id, id);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  origin TEXT NOT NULL DEFAULT 'body',
  source_key TEXT NOT NULL,
  served_url TEXT,
  original_url TEXT,
  poster_url TEXT,
  url_expires_at TEXT,
  width INTEGER,
  height INTEGER,
  file_path TEXT,
  file_size INTEGER,
  sha256 TEXT,
  content_type TEXT,
  state TEXT NOT NULL DEFAULT 'pending',
  state_code TEXT,
  state_message TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  created_at TEXT NOT NULL,
  downloaded_at TEXT,
  is_original INTEGER,
  original_unavailable INTEGER NOT NULL DEFAULT 0,
  upgrade_attempts INTEGER NOT NULL DEFAULT 0,
  UNIQUE(article_id, source_key)
);
CREATE INDEX IF NOT EXISTS idx_media_state ON media(state, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_media_article ON media(article_id, seq);
CREATE INDEX IF NOT EXISTS idx_media_sha ON media(sha256);

CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  remote_id TEXT,
  parent_id INTEGER,
  author TEXT,
  created_at TEXT,
  body_html TEXT,
  body_text TEXT,
  is_deleted INTEGER NOT NULL DEFAULT 0,
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_article ON comments(article_id, id);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
  channel_slug TEXT,
  trigger TEXT NOT NULL,
  status TEXT NOT NULL,
  code TEXT,
  message TEXT,
  stage TEXT,
  fetcher TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  at TEXT NOT NULL,
  level TEXT NOT NULL,
  event TEXT NOT NULL,
  article_id INTEGER,
  media_id INTEGER,
  data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_run_events_level ON run_events(level, id);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queued_runs (
  channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE,
  trigger TEXT NOT NULL,
  requested_at TEXT NOT NULL
);
""" + ";".join(BOOKMARK_SCHEMA + COMMENT_FETCH_SCHEMA) + ";"

ARTICLE_STATES = ("discovered", "collected", "blocked", "deleted", "failed", "error")
MEDIA_STATES = ("pending", "downloaded", "failed", "expired", "skipped", "error")
RUN_STATUSES = ("running", "success", "partial", "failed", "cancelled")


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------ 연결
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self):
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def _init_schema(self) -> None:
        conn = self.connect()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            return
        if version < 2:
            # v2: 미디어 원본 여부/승격 추적 컬럼
            for column, ddl in (("is_original", "INTEGER"), ("original_unavailable", "INTEGER NOT NULL DEFAULT 0"),
                                ("upgrade_attempts", "INTEGER NOT NULL DEFAULT 0")):
                if column not in self._columns(conn, "media"):
                    conn.execute(f"ALTER TABLE media ADD COLUMN {column} {ddl}")
            conn.execute("PRAGMA user_version=2")
        if version < 3:
            # v3: 채널 백필(과거 글 더 가져오기) 요청 페이지 수
            if "backfill_pages" not in self._columns(conn, "channels"):
                conn.execute("ALTER TABLE channels ADD COLUMN backfill_pages INTEGER NOT NULL DEFAULT 0")
            conn.execute("PRAGMA user_version=3")
        if version < 4:
            # v4: 다중 사이트 지원 — 채널 site/site_channel_id, 글·댓글 원격 ID
            if "site" not in self._columns(conn, "channels"):
                conn.execute("ALTER TABLE channels ADD COLUMN site TEXT NOT NULL DEFAULT 'arca'")
            if "site_channel_id" not in self._columns(conn, "channels"):
                conn.execute("ALTER TABLE channels ADD COLUMN site_channel_id TEXT")
            if "remote_id" not in self._columns(conn, "articles"):
                conn.execute("ALTER TABLE articles ADD COLUMN remote_id TEXT")
            if "remote_id" not in self._columns(conn, "comments"):
                conn.execute("ALTER TABLE comments ADD COLUMN remote_id TEXT")
            conn.execute("UPDATE channels SET site_channel_id=slug WHERE site_channel_id IS NULL")
            conn.execute("UPDATE articles SET remote_id=CAST(id AS TEXT) WHERE remote_id IS NULL")
            conn.execute("UPDATE comments SET remote_id=CAST(id AS TEXT) WHERE remote_id IS NULL")
            conn.execute("PRAGMA user_version=4")
        if version < 5:
            if "next_media_at" not in self._columns(conn, "channels"):
                conn.execute("ALTER TABLE channels ADD COLUMN next_media_at TEXT")
            conn.execute("CREATE TABLE IF NOT EXISTS queued_runs (channel_id INTEGER PRIMARY KEY REFERENCES channels(id) ON DELETE CASCADE, trigger TEXT NOT NULL, requested_at TEXT NOT NULL)")
            conn.execute("PRAGMA user_version=5")
        if version < 6:
            with self.transaction():
                for statement in BOOKMARK_SCHEMA:
                    conn.execute(statement)
                conn.execute("PRAGMA user_version=6")
        if version < 7:
            with self.transaction():
                for statement in COMMENT_FETCH_SCHEMA:
                    conn.execute(statement)
                # 과거 버전에서 본문 저장 후 댓글 수집이 중단된 글도 다시 확인합니다.
                # 개수 차이는 누락 확정이 아니므로 '확인 대기'로만 기록합니다.
                if version >= 4:  # 디시 데이터는 다중 사이트 스키마(v4) 이후에만 존재합니다.
                    conn.execute("""INSERT OR IGNORE INTO comment_fetches
                        (article_id,state,expected_count,saved_count,code)
                        SELECT a.id,'pending',a.comment_count,COUNT(cm.id),'LEGACY_UNVERIFIED'
                        FROM articles a JOIN channels ch ON ch.id=a.channel_id
                        LEFT JOIN comments cm ON cm.article_id=a.id
                        WHERE ch.site='dcinside' AND a.state='collected' AND a.comment_count>0
                        GROUP BY a.id HAVING COUNT(cm.id)<a.comment_count""")
                conn.execute("PRAGMA user_version=7")

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    # ------------------------------------------------------------------ 공통
    def _update(self, table: str, row_id: int, fields: dict[str, Any], id_column: str = "id") -> None:
        if not fields:
            return
        columns = ", ".join(f"{k}=?" for k in fields)
        self.connect().execute(f"UPDATE {table} SET {columns} WHERE {id_column}=?", [*fields.values(), row_id])

    # ------------------------------------------------------------------ 채널
    def list_channels(self) -> list[dict]:
        rows = self.connect().execute("SELECT * FROM channels ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def get_channel(self, channel_id: int) -> dict | None:
        return _row_to_dict(self.connect().execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone())

    def get_channel_by_slug(self, slug: str) -> dict | None:
        return _row_to_dict(self.connect().execute("SELECT * FROM channels WHERE slug=?", (slug,)).fetchone())

    def create_channel(self, slug: str, **fields: Any) -> dict:
        stamp = now_iso()
        fields.setdefault("site", "arca")
        fields.setdefault("site_channel_id", slug)
        allowed = {k: v for k, v in fields.items() if k in CHANNEL_EDITABLE}
        columns = ["slug", "created_at", "updated_at", *allowed]
        values = [slug, stamp, stamp, *allowed.values()]
        with self.transaction() as conn:
            conn.execute(
                f"INSERT INTO channels ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values
            )
        return self.get_channel_by_slug(slug)  # type: ignore[return-value]

    def update_channel(self, channel_id: int, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in CHANNEL_EDITABLE or k in CHANNEL_RUNTIME}
        allowed["updated_at"] = now_iso()
        with self.transaction():
            self._update("channels", channel_id, allowed)

    def delete_channel(self, channel_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM channels WHERE id=?", (channel_id,))

    # ------------------------------------------------------------------ 게시글
    def get_article(self, article_id: int) -> dict | None:
        return _row_to_dict(self.connect().execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone())

    def known_article_ids(self, channel_id: int, ids: Iterable[int]) -> set[int]:
        ids = list(ids)
        if not ids:
            return set()
        marks = ",".join("?" for _ in ids)
        rows = self.connect().execute(
            f"SELECT id FROM articles WHERE channel_id=? AND id IN ({marks})", [channel_id, *ids]
        ).fetchall()
        return {r[0] for r in rows}

    def upsert_discovered(self, channel_id: int, rows: list[dict], run_id: int | None) -> list[int]:
        """목록에서 본 글을 등록합니다. 이미 있는 글은 목록 지표(조회수 등)만 갱신합니다.

        rows 항목: id(내부 ID), remote_id, url, title, category, badges, author, is_notice, created_at, view_count,
                   like_count, comment_count, channel_local_no
        반환: 새로 등록된 article id 목록
        """
        stamp = now_iso()
        new_ids: list[int] = []
        with self.transaction() as conn:
            for row in rows:
                existing = conn.execute("SELECT id, state FROM articles WHERE id=?", (row["id"],)).fetchone()
                if existing is None:
                    conn.execute(
                        """INSERT INTO articles (id, channel_id, remote_id, channel_local_no, url, title, category, badges, author,
                              is_notice, created_at, view_count, like_count, comment_count, state, discovered_at,
                              discovered_run_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'discovered',?,?)""",
                        (row["id"], channel_id, str(row.get("remote_id") or row["id"]), row.get("channel_local_no"), row["url"], row.get("title"),
                         row.get("category"), json.dumps(row.get("badges") or [], ensure_ascii=False), row.get("author"),
                         1 if row.get("is_notice") else 0, row.get("created_at"), row.get("view_count"),
                         row.get("like_count"), row.get("comment_count"), stamp, run_id),
                    )
                    new_ids.append(row["id"])
                else:
                    conn.execute(
                        """UPDATE articles SET view_count=COALESCE(?, view_count), like_count=COALESCE(?, like_count),
                              comment_count=COALESCE(?, comment_count), title=COALESCE(title, ?),
                              channel_local_no=COALESCE(channel_local_no, ?)
                           WHERE id=?""",
                        (row.get("view_count"), row.get("like_count"), row.get("comment_count"), row.get("title"),
                         row.get("channel_local_no"), row["id"]),
                    )
        return new_ids

    def pending_articles(self, channel_id: int, limit: int, now: str | None = None) -> list[dict]:
        if limit <= 0:
            return []
        now = now or now_iso()
        conn = self.connect()
        retries = conn.execute(
            """SELECT * FROM articles
               WHERE channel_id=? AND state='failed' AND (next_retry_at IS NULL OR next_retry_at<=?)
               ORDER BY next_retry_at ASC, last_checked_at ASC, id DESC LIMIT ?""",
            (channel_id, now, limit),
        ).fetchall()
        # 실행당 최소 한 자리(전체 한도의 20%)를 기한이 지난 재시도에 배정합니다.
        # 새 글도 계속 수집하고, 남는 자리는 다른 재시도로 채웁니다.
        reserved = min(len(retries), max(1, limit // 5))
        rows = list(retries[:reserved])
        rows.extend(conn.execute(
            "SELECT * FROM articles WHERE channel_id=? AND state='discovered' ORDER BY id DESC LIMIT ?",
            (channel_id, limit - reserved),
        ).fetchall())
        rows.extend(retries[reserved:reserved + limit - len(rows)])
        return [dict(r) for r in rows]

    def recheck_candidates(self, channel_id: int, created_since: str, checked_before: str, limit: int) -> list[dict]:
        rows = self.connect().execute(
            """SELECT * FROM articles
               WHERE channel_id=? AND state='collected' AND created_at>=? AND (last_checked_at IS NULL OR last_checked_at<=?)
               ORDER BY last_checked_at ASC, id DESC LIMIT ?""",
            (channel_id, created_since, checked_before, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def expired_media_articles(self, channel_id: int, limit: int, max_upgrade_attempts: int = 3,
                               include_upgrades: bool = True) -> list[dict]:
        """미디어를 다시 받아야 하는 글: 만료/실패/대기 항목, 그리고(허용 시) 원본 승격이 남은 변환본이 있는 글."""
        upgrade_clause = ""
        stamp = now_iso()
        params: list = [channel_id, stamp, stamp]
        if include_upgrades:
            upgrade_clause = """ OR (m.state='downloaded' AND m.is_original=0 AND m.original_unavailable=0
                          AND m.original_url IS NOT NULL AND m.upgrade_attempts<?)"""
            params.append(max_upgrade_attempts)
        params.append(limit)
        rows = self.connect().execute(
            f"""SELECT a.* FROM articles a JOIN media m ON m.article_id=a.id
                WHERE a.channel_id=? AND a.state='collected'
                  AND (a.next_retry_at IS NULL OR a.next_retry_at<=?)
                  AND m.kind IN ('image','gif','video','emoticon')
                 AND ((m.state IN ('expired','failed','pending') AND (m.next_retry_at IS NULL OR m.next_retry_at<=?)){upgrade_clause})
                GROUP BY a.id
                ORDER BY MIN(CASE WHEN m.state IN ('expired','failed')
                                      OR m.state_code IN ('URL_EXPIRED','RETRY_REQUESTED','DOWNLOAD_PAUSED') THEN 0
                                  WHEN m.state='pending' THEN 1 ELSE 2 END),
                         a.last_checked_at ASC, a.id DESC LIMIT ?""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def upgrade_candidates_for_article(self, article_id: int, max_upgrade_attempts: int = 3) -> list[dict]:
        rows = self.connect().execute(
            """SELECT * FROM media WHERE article_id=? AND state='downloaded' AND is_original=0 AND original_unavailable=0
               AND original_url IS NOT NULL AND upgrade_attempts<? ORDER BY seq, id""",
            (article_id, max_upgrade_attempts),
        ).fetchall()
        return [dict(r) for r in rows]

    def media_referencing_path(self, path: str) -> int:
        return self.connect().execute("SELECT COUNT(*) FROM media WHERE file_path=?", (path,)).fetchone()[0]

    def update_article(self, article_id: int, **fields: Any) -> None:
        with self.transaction():
            self._update("articles", article_id, fields)

    def save_collected(self, article_id: int, data: dict, run_id: int | None, raw_html_path: str | None) -> dict:
        """파싱 결과를 저장합니다. 본문 해시가 바뀐 기존 글은 이전 본문을 revision에 남깁니다.

        반환: {"changed": bool, "first": bool}
        """
        stamp = now_iso()
        with self.transaction() as conn:
            current = conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()
            first = current is None or current["collected_at"] is None
            changed = False
            if current is not None and current["body_hash"] and data.get("body_hash") and current["body_hash"] != data["body_hash"]:
                changed = True
                conn.execute(
                    """INSERT INTO article_revisions (article_id, captured_at, title, body_html, body_text, body_hash, run_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (article_id, current["collected_at"] or stamp, current["title"], current["body_html"],
                     current["body_text"], current["body_hash"], run_id),
                )
            conn.execute(
                """UPDATE articles SET title=?, category=?, badges=?, author=?, author_type=?, created_at=COALESCE(?, created_at),
                      edited_at=?, view_count=?, like_count=?, dislike_count=?, comment_count=?, body_html=?, body_text=?,
                      body_hash=?, state='collected', state_code=NULL, state_message=NULL, attempts=0, next_retry_at=NULL,
                      collected_at=CASE WHEN collected_at IS NULL THEN ? ELSE collected_at END, last_checked_at=?,
                      raw_html_path=COALESCE(?, raw_html_path), collected_run_id=?,
                      revision_count=revision_count + ?
                   WHERE id=?""",
                (data.get("title"), data.get("category"), json.dumps(data.get("badges") or [], ensure_ascii=False),
                 data.get("author"), data.get("author_type"), data.get("created_at"), data.get("edited_at"),
                 data.get("view_count"), data.get("like_count"), data.get("dislike_count"), data.get("comment_count"),
                 data.get("body_html"), data.get("body_text"), data.get("body_hash"), stamp, stamp, raw_html_path,
                 run_id, 1 if changed else 0, article_id),
            )
        return {"changed": changed, "first": first}

    def mark_article_failure(self, article_id: int, state: str, code: str, message: str, next_retry_at: str | None,
                             attempts: int | None = None) -> None:
        fields: dict[str, Any] = {"state": state, "state_code": code, "state_message": message[:500],
                                  "next_retry_at": next_retry_at, "last_checked_at": now_iso()}
        if attempts is not None:
            fields["attempts"] = attempts
        if state == "deleted":
            fields["deleted_at"] = now_iso()
        with self.transaction():
            self._update("articles", article_id, fields)

    def list_articles(self, channel_id: int | None = None, state: str | None = None, query: str | None = None,
                      limit: int = 50, offset: int = 0, *, site: str | None = None,
                      scope: str = 'all', date_from: str | None = None, date_to: str | None = None,
                      media_state: str | None = None) -> tuple[list[dict], int]:
        where, params = [], []
        if channel_id:
            where.append("a.channel_id=?")
            params.append(channel_id)
        if state:
            where.append("a.state=?")
            params.append(state)
        if query:
            columns = {'title': ['a.title'], 'body': ['a.body_text'], 'author': ['a.author'],
                       'comments': []}.get(scope, ['a.title', 'a.body_text', 'a.author'])
            terms = [f"instr(lower(COALESCE({col},'')),lower(?))>0" for col in columns]
            params.extend([query] * len(columns))
            if scope in ('all', 'comments'):
                terms.append("EXISTS(SELECT 1 FROM comments cm WHERE cm.article_id=a.id AND instr(lower(COALESCE(cm.body_text,'')),lower(?))>0)")
                params.append(query)
            where.append('(' + ' OR '.join(terms) + ')')
        if site:
            where.append("a.channel_id IN (SELECT id FROM channels WHERE site=?)")
            params.append(site)
        if date_from:
            where.append("date(a.created_at,'+9 hours')>=?")
            params.append(date_from)
        if date_to:
            where.append("date(a.created_at,'+9 hours')<=?")
            params.append(date_to)
        if media_state in ('downloaded', 'unfinished'):
            cond = "mm.state='downloaded'" if media_state == 'downloaded' else "mm.state IN ('pending','expired','failed','error')"
            where.append(f"EXISTS(SELECT 1 FROM media mm WHERE mm.article_id=a.id AND {cond})")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self.connect()
        total = conn.execute(f"SELECT COUNT(*) FROM articles a {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT a.*, c.slug AS channel_slug FROM articles a JOIN channels c ON c.id=a.channel_id
                {clause} ORDER BY COALESCE(a.created_at,a.discovered_at) DESC,a.id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def count_articles_by_state(self, channel_id: int | None = None) -> dict[str, int]:
        if channel_id:
            rows = self.connect().execute(
                "SELECT state, COUNT(*) FROM articles WHERE channel_id=? GROUP BY state", (channel_id,)).fetchall()
        else:
            rows = self.connect().execute("SELECT state, COUNT(*) FROM articles GROUP BY state").fetchall()
        return {r[0]: r[1] for r in rows}

    def list_revisions(self, article_id: int) -> list[dict]:
        rows = self.connect().execute(
            "SELECT * FROM article_revisions WHERE article_id=? ORDER BY id", (article_id,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 미디어
    def upsert_media(self, article_id: int, items: list[dict]) -> list[dict]:
        """본문에서 발견한 미디어를 등록/갱신합니다. 이미 내려받은 항목은 URL만 갱신합니다."""
        stamp = now_iso()
        with self.transaction() as conn:
            for item in items:
                existing = conn.execute(
                    "SELECT id, state, state_code FROM media WHERE article_id=? AND source_key=?", (article_id, item["source_key"])
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """INSERT INTO media (article_id, seq, kind, origin, source_key, served_url, original_url, poster_url,
                              url_expires_at, width, height, state, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (article_id, item["seq"], item["kind"], item.get("origin", "body"), item["source_key"],
                         item.get("served_url"), item.get("original_url"), item.get("poster_url"),
                         item.get("url_expires_at"), item.get("width"), item.get("height"),
                         item.get("state", "pending"), stamp),
                    )
                else:
                    new_state = existing["state"]
                    if new_state == "expired" or existing["state_code"] == "SOURCE_NOT_PRESENT":
                        new_state = "pending"
                    conn.execute(
                        """UPDATE media SET seq=?, kind=?, served_url=?, original_url=?, poster_url=?, url_expires_at=?,
                              width=COALESCE(?, width), height=COALESCE(?, height), state=?
                           WHERE id=?""",
                        (item["seq"], item["kind"], item.get("served_url"), item.get("original_url"),
                         item.get("poster_url"), item.get("url_expires_at"), item.get("width"), item.get("height"),
                         new_state, existing["id"]),
                    )
            self._refresh_media_counts(conn, article_id)
        return self.list_media(article_id)

    def reconcile_body_media(self, article_id: int, source_keys: set[str]) -> int:
        """이번에 읽은 본문에 없는 미수집 참조를 명시적으로 보류합니다. 파일은 지우지 않습니다."""
        with self.transaction() as conn:
            missing = [r["id"] for r in conn.execute("SELECT id,source_key FROM media WHERE article_id=? AND origin='body' AND state IN ('pending','expired','failed')", (article_id,)) if r["source_key"] not in source_keys]
            conn.executemany("UPDATE media SET state='error',state_code='SOURCE_NOT_PRESENT',state_message='최신 본문에서 이 참조를 찾지 못했습니다. 보관된 참조는 유지합니다.',next_retry_at=NULL WHERE id=?", [(mid,) for mid in missing])
        return len(missing)

    def _refresh_media_counts(self, conn: sqlite3.Connection, article_id: int) -> None:
        conn.execute(
            """UPDATE articles SET
                 media_total=(SELECT COUNT(*) FROM media WHERE article_id=? AND kind IN ('image','gif','video','emoticon')),
                 media_done=(SELECT COUNT(*) FROM media WHERE article_id=? AND state='downloaded')
               WHERE id=?""",
            (article_id, article_id, article_id),
        )

    def list_media(self, article_id: int) -> list[dict]:
        rows = self.connect().execute("SELECT * FROM media WHERE article_id=? ORDER BY seq, id", (article_id,)).fetchall()
        return [dict(r) for r in rows]

    def pending_media_for_article(self, article_id: int, now: str | None = None) -> list[dict]:
        now = now or now_iso()
        rows = self.connect().execute(
            """SELECT * FROM media WHERE article_id=? AND state IN ('pending','failed') AND (next_retry_at IS NULL OR next_retry_at<=?)
               ORDER BY CASE WHEN state='failed' OR state_code IN ('URL_EXPIRED','RETRY_REQUESTED','DOWNLOAD_PAUSED') THEN 0 ELSE 1 END, seq, id""",
            (article_id, now),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_media(self, media_id: int, **fields: Any) -> None:
        with self.transaction() as conn:
            self._update("media", media_id, fields)
            row = conn.execute("SELECT article_id FROM media WHERE id=?", (media_id,)).fetchone()
            if row:
                self._refresh_media_counts(conn, row[0])

    def find_media_by_sha(self, sha256: str) -> dict | None:
        return _row_to_dict(self.connect().execute(
            "SELECT * FROM media WHERE sha256=? AND state='downloaded' AND file_path IS NOT NULL LIMIT 1", (sha256,)).fetchone())

    def list_media_page(self, channel_id: int | None = None, kind: str | None = None, state: str | None = None,
                        limit: int = 60, offset: int = 0) -> tuple[list[dict], int]:
        where, params = [], []
        if channel_id:
            where.append("a.channel_id=?")
            params.append(channel_id)
        if kind:
            where.append("m.kind=?")
            params.append(kind)
        if state:
            where.append("m.state=?")
            params.append(state)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self.connect()
        total = conn.execute(f"SELECT COUNT(*) FROM media m JOIN articles a ON a.id=m.article_id {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT m.*, a.title AS article_title, a.channel_id, c.slug AS channel_slug
                FROM media m JOIN articles a ON a.id=m.article_id JOIN channels c ON c.id=a.channel_id
                {clause} ORDER BY m.article_id DESC, m.seq LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def media_bytes(self, channel_id: int | None = None) -> int:
        if channel_id:
            row = self.connect().execute(
                "SELECT COALESCE(SUM(m.file_size),0) FROM media m JOIN articles a ON a.id=m.article_id WHERE a.channel_id=? AND m.state='downloaded'",
                (channel_id,)).fetchone()
        else:
            row = self.connect().execute("SELECT COALESCE(SUM(file_size),0) FROM media WHERE state='downloaded'").fetchone()
        return int(row[0] or 0)

    def count_media_by_state(self, channel_id: int | None = None) -> dict[str, int]:
        if channel_id:
            rows = self.connect().execute(
                "SELECT m.state, COUNT(*) FROM media m JOIN articles a ON a.id=m.article_id WHERE a.channel_id=? GROUP BY m.state",
                (channel_id,)).fetchall()
        else:
            rows = self.connect().execute("SELECT state, COUNT(*) FROM media GROUP BY state").fetchall()
        return {r[0]: r[1] for r in rows}

    def get_media(self, media_id: int) -> dict | None:
        return _row_to_dict(self.connect().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone())

    # ------------------------------------------------------------------ 댓글
    def replace_comments(self, article_id: int, comments: list[dict]) -> int:
        """댓글 항목: id(내부 ID), remote_id, parent_id(내부 ID), author, created_at, body_html, body_text, is_deleted"""
        stamp = now_iso()
        with self.transaction() as conn:
            for c in comments:
                conn.execute(
                    """INSERT INTO comments (id, article_id, remote_id, parent_id, author, created_at, body_html, body_text, is_deleted, captured_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, author=excluded.author,
                         created_at=excluded.created_at, body_html=excluded.body_html, body_text=excluded.body_text,
                         is_deleted=excluded.is_deleted, captured_at=excluded.captured_at""",
                    (c["id"], article_id, str(c.get("remote_id") or c["id"]), c.get("parent_id"), c.get("author"), c.get("created_at"),
                     c.get("body_html"), c.get("body_text"), 1 if c.get("is_deleted") else 0, stamp),
                )
        return len(comments)

    def list_comments(self, article_id: int) -> list[dict]:
        rows = self.connect().execute("SELECT * FROM comments WHERE article_id=? ORDER BY id", (article_id,)).fetchall()
        return [dict(r) for r in rows]

    def comment_fetch(self, article_id: int) -> dict | None:
        return _row_to_dict(self.connect().execute(
            "SELECT * FROM comment_fetches WHERE article_id=?", (article_id,)).fetchone())

    def save_comment_fetch(self, article_id: int, state: str, expected: int, saved: int,
                           attempts: int = 0, code: str | None = None, next_retry_at: str | None = None) -> None:
        self.connect().execute("""INSERT INTO comment_fetches
            (article_id,state,expected_count,saved_count,attempts,code,checked_at,next_retry_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET
            state=excluded.state,expected_count=excluded.expected_count,saved_count=excluded.saved_count,
            attempts=excluded.attempts,code=excluded.code,checked_at=excluded.checked_at,next_retry_at=excluded.next_retry_at""",
            (article_id,state,expected,saved,attempts,code,now_iso(),next_retry_at))

    def comment_retry_candidates(self, channel_id: int, limit: int) -> list[dict]:
        return [dict(r) for r in self.connect().execute("""SELECT a.* FROM comment_fetches cf
            JOIN articles a ON a.id=cf.article_id WHERE a.channel_id=? AND a.state='collected'
            AND cf.state IN ('pending','incomplete') AND (cf.next_retry_at IS NULL OR cf.next_retry_at<=?)
            ORDER BY cf.checked_at ASC,a.id LIMIT ?""", (channel_id,now_iso(),limit))]

    def incomplete_comments(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.connect().execute("""SELECT cf.*,a.title,ch.slug AS channel_slug,
            ch.enabled,ch.collect_comments FROM comment_fetches cf
            JOIN articles a ON a.id=cf.article_id JOIN channels ch ON ch.id=a.channel_id
            WHERE cf.state!='complete' AND a.state='collected'
            ORDER BY cf.checked_at DESC,a.id DESC LIMIT ?""", (limit,))]

    def requeue_comments(self, article_id: int) -> bool:
        cursor = self.connect().execute("""UPDATE comment_fetches
            SET state='pending',attempts=0,code='RETRY_REQUESTED',next_retry_at=NULL
            WHERE article_id=? AND state!='complete'""", (article_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ 실행
    def create_run(self, channel: dict | None, trigger: str) -> dict:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO runs (channel_id, channel_slug, trigger, status, stage, started_at) VALUES (?,?,?,?,?,?)",
                (channel["id"] if channel else None, channel["slug"] if channel else None, trigger, "running", "start", now_iso()),
            )
            run_id = cur.lastrowid
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: int) -> dict | None:
        row = _row_to_dict(self.connect().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        if row:
            row["stats"] = json.loads(row.get("stats_json") or "{}")
        return row

    def update_run(self, run_id: int, stats: dict | None = None, **fields: Any) -> None:
        if stats is not None:
            fields["stats_json"] = json.dumps(stats, ensure_ascii=False)
        with self.transaction():
            self._update("runs", run_id, fields)

    def finish_run(self, run_id: int, status: str, code: str | None, message: str | None, stats: dict) -> None:
        self.update_run(run_id, stats=stats, status=status, code=code, message=(message or "")[:500],
                        finished_at=now_iso(), stage="finished")

    def list_runs(self, limit: int = 50, channel_id: int | None = None) -> list[dict]:
        if channel_id:
            rows = self.connect().execute(
                "SELECT * FROM runs WHERE channel_id=? ORDER BY id DESC LIMIT ?", (channel_id, limit)).fetchall()
        else:
            rows = self.connect().execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d.get("stats_json") or "{}")
            result.append(d)
        return result

    def active_runs(self) -> list[dict]:
        rows = self.connect().execute("SELECT * FROM runs WHERE status='running' ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def queue_run(self, channel_id: int, trigger: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO queued_runs VALUES(?,?,?)", (channel_id, trigger, now_iso()))

    def queued_runs(self) -> list[dict]:
        return [dict(r) for r in self.connect().execute("SELECT * FROM queued_runs ORDER BY requested_at,channel_id")]

    def clear_queued_runs(self, channel_id: int | None = None, scheduled_only: bool = False) -> None:
        with self.transaction() as conn:
            if channel_id is None:
                conn.execute("DELETE FROM queued_runs")
            else:
                clause = " AND trigger IN ('schedule','backlog')" if scheduled_only else ""
                conn.execute("DELETE FROM queued_runs WHERE channel_id=?" + clause, (channel_id,))

    def media_work_channels(self) -> dict[int, str]:
        rows = self.connect().execute("""SELECT c.id, MIN(m.created_at) oldest
            FROM channels c JOIN articles a ON a.channel_id=c.id JOIN media m ON m.article_id=a.id
            WHERE c.enabled=1 AND c.collect_media=1 AND a.state='collected'
              AND m.state IN ('pending','failed','expired')
               AND (m.next_retry_at IS NULL OR m.next_retry_at<=?)
               AND (a.next_retry_at IS NULL OR a.next_retry_at<=?)
              AND m.kind IN ('image','gif','video','emoticon')
            GROUP BY c.id""", (now_iso(), now_iso()))
        return {r["id"]: r["oldest"] for r in rows}

    def media_snapshot(self, channel_id: int) -> dict:
        row = self.connect().execute("""SELECT COALESCE(MAX(m.id),0) max_id,COUNT(*) total,
            SUM(m.state='downloaded') downloaded,
            SUM(m.state IN ('pending','expired','failed','error')) unfinished
            FROM media m JOIN articles a ON a.id=m.article_id WHERE a.channel_id=?""", (channel_id,)).fetchone()
        return {k: int(row[k] or 0) for k in row.keys()}

    def abort_stale_runs(self, message: str) -> int:
        """프로세스 재시작 후 남아 있는 running 상태를 정리합니다."""
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE runs SET status='failed', code='INTERRUPTED', message=?, finished_at=? WHERE status='running'",
                (message, now_iso()),
            )
            return cur.rowcount

    # ------------------------------------------------------------------ 이벤트
    def add_event(self, run_id: int, level: str, event: str, article_id: int | None = None,
                  media_id: int | None = None, **data: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO run_events (run_id, at, level, event, article_id, media_id, data_json) VALUES (?,?,?,?,?,?,?)",
                (run_id, now_iso(), level, event, article_id, media_id,
                 json.dumps(data, ensure_ascii=False, default=str) if data else None),
            )

    def list_events(self, run_id: int, limit: int = 500, level: str | None = None, after_id: int = 0) -> list[dict]:
        if level:
            rows = self.connect().execute(
                "SELECT * FROM run_events WHERE run_id=? AND level=? AND id>? ORDER BY id LIMIT ?",
                (run_id, level, after_id, limit)).fetchall()
        else:
            rows = self.connect().execute(
                "SELECT * FROM run_events WHERE run_id=? AND id>? ORDER BY id LIMIT ?", (run_id, after_id, limit)).fetchall()
        return [self._event_dict(r) for r in rows]

    def purge_old_events(self, days: int) -> int:
        cutoff = (utcnow() - timedelta(days=days)).replace(microsecond=0).isoformat()
        with self.transaction() as conn:
            return conn.execute("DELETE FROM run_events WHERE at < ?", (cutoff,)).rowcount

    def recent_error_events(self, limit: int = 100) -> list[dict]:
        rows = self.connect().execute(
            "SELECT * FROM run_events WHERE level IN ('error','warning') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._event_dict(r) for r in rows]

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["data"] = json.loads(d.pop("data_json") or "{}")
        return d

    # ------------------------------------------------------------------ 설정(KV)
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.connect().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now_iso()),
            )

    # ------------------------------------------------------------------ 백업
    def backup_to(self, destination: Path) -> Path:
        """실행 중에도 일관된 스냅샷을 만드는 SQLite 온라인 백업."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connect().backup(target)
        finally:
            target.close()
        return destination

    # ------------------------------------------------------------------ 북마크
    @staticmethod
    def _bookmark_target(kind: str) -> tuple[str, str]:
        if kind not in ("article", "media"):
            raise ValueError("북마크 종류가 올바르지 않습니다")
        return ("articles" if kind == "article" else "media", f"{kind}_id")

    def set_bookmark(self, kind: str, target_id: int, saved: bool) -> None:
        """참조만 저장합니다. 수집 상태나 파일은 바꾸지 않으며 중복 요청도 안전합니다."""
        table, column = self._bookmark_target(kind)
        with self.transaction() as conn:
            if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (target_id,)).fetchone():
                raise LookupError("북마크할 항목을 찾을 수 없습니다")
            if saved:
                conn.execute(
                    f"INSERT INTO bookmarks ({column}, created_at) VALUES (?,?) ON CONFLICT({column}) DO NOTHING",
                    (target_id, now_iso()),
                )
            else:
                conn.execute(f"DELETE FROM bookmarks WHERE {column}=?", (target_id,))

    def bookmarked_ids(self, kind: str, ids: Iterable[int]) -> set[int]:
        _, column = self._bookmark_target(kind)
        ids = list(set(ids))
        if not ids:
            return set()
        rows = self.connect().execute(
            f"SELECT {column} FROM bookmarks WHERE {column} IN ({','.join('?' for _ in ids)})", ids,
        )
        return {row[0] for row in rows}

    def list_bookmarks(self, *, kind: str = "", channel_id: int | None = None, media_kind: str = "",
                       query: str = "", sort: str = "newest", limit: int = 60, offset: int = 0) -> tuple[list[dict], int]:
        """글의 모든 본문 미디어와 개별 북마크를 펼쳐 페이지 단위로 보여줍니다.

        반환 total은 펼친 표시 항목 수입니다. 미디어가 없는 글도 한 카드로 남깁니다.
        종류 필터는 펼친 각 파일에 적용하며 한 글 안에서는 본문 순서를 유지합니다.
        미수집 항목도 보존하며 내려받기가 끝나면 같은 참조에 썸네일이 나타납니다.
        """
        joins = """FROM bookmarks b
            LEFT JOIN media saved_media ON saved_media.id=b.media_id
            JOIN articles a ON a.id=COALESCE(b.article_id,saved_media.article_id)
            JOIN channels c ON c.id=a.channel_id
            LEFT JOIN media m ON m.id=b.media_id OR (m.article_id=b.article_id AND m.origin='body')"""
        where, params = [], []
        if kind in ("article", "media"):
            where.append(f"b.{kind}_id IS NOT NULL")
        if channel_id:
            where.append("a.channel_id=?")
            params.append(channel_id)
        if media_kind:
            where.append("m.kind=?")
            params.append(media_kind)
        if query:
            where.append("""(instr(lower(COALESCE(a.title,'')),lower(?))>0
                OR instr(lower(COALESCE(a.body_text,'')),lower(?))>0
                OR instr(lower(COALESCE(a.author,'')),lower(?))>0)""")
            params.extend([query] * 3)
        clause = " WHERE " + " AND ".join(where) if where else ""
        conn = self.connect()
        total = conn.execute(f"SELECT COUNT(*) {joins}{clause}", params).fetchone()[0]
        direction = "ASC" if sort == "oldest" else "DESC"
        rows = conn.execute(
            f"""SELECT b.id AS bookmark_id, b.created_at AS bookmarked_at,
                CASE WHEN b.article_id IS NOT NULL THEN 'article' ELSE 'media' END AS bookmark_kind,
                COALESCE(b.article_id,b.media_id) AS target_id,
                a.id AS article_id, a.title AS article_title, a.author, substr(a.body_text,1,200) AS excerpt,
                a.channel_id, c.slug AS channel_slug, c.site,
                m.id AS media_id, m.seq, m.kind, m.state, m.file_path, m.content_type, m.file_size,
                EXISTS(SELECT 1 FROM bookmarks ab WHERE ab.article_id=a.id) AS article_bookmarked,
                EXISTS(SELECT 1 FROM bookmarks mb WHERE mb.media_id=m.id) AS media_bookmarked
                {joins} {clause}
                ORDER BY b.created_at {direction},b.id {direction},m.seq ASC,m.id ASC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return [dict(row) for row in rows], total

    # ------------------------------------------------------------------ 오류 목록
    def failed_items(self, limit: int = 200) -> dict[str, list[dict]]:
        conn = self.connect()
        articles = conn.execute(
            """SELECT a.id, a.title, a.state, a.state_code, a.state_message, a.attempts, a.next_retry_at, a.last_checked_at,
                      a.url, c.slug AS channel_slug
               FROM articles a JOIN channels c ON c.id=a.channel_id
               WHERE a.state IN ('failed','error','blocked') ORDER BY a.last_checked_at DESC LIMIT ?""",
            (limit,)).fetchall()
        media = conn.execute(
            """SELECT m.id, m.article_id, m.kind, m.state, m.state_code, m.state_message, m.attempts, m.next_retry_at,
                      m.source_key, a.title AS article_title, c.slug AS channel_slug
               FROM media m JOIN articles a ON a.id=m.article_id JOIN channels c ON c.id=a.channel_id
               WHERE m.state IN ('failed','error','expired') ORDER BY m.id DESC LIMIT ?""",
            (limit,)).fetchall()
        return {"articles": [dict(r) for r in articles], "media": [dict(r) for r in media]}

    def requeue_articles(self, channel_id: int | None = None, states: tuple[str, ...] = ("failed", "error", "blocked")) -> int:
        marks = ",".join("?" for _ in states)
        params: list[Any] = list(states)
        clause = ""
        if channel_id:
            clause = " AND channel_id=?"
            params.append(channel_id)
        with self.transaction() as conn:
            cur = conn.execute(
                f"UPDATE articles SET state='failed', attempts=0, next_retry_at=NULL, state_code='RETRY_REQUESTED', "
                f"state_message='수동 재시도를 요청했습니다. 채널 실행 시 우선 처리합니다.' WHERE state IN ({marks}){clause}",
                params,
            )
            return cur.rowcount

    def requeue_media(self, article_id: int | None = None) -> int:
        with self.transaction() as conn:
            if article_id:
                cur = conn.execute(
                    "UPDATE media SET state='pending', attempts=0, next_retry_at=NULL, state_code='RETRY_REQUESTED', "
                    "state_message='수동 재시도를 요청했습니다.' WHERE article_id=? AND state IN ('failed','error','expired')",
                    (article_id,))
            else:
                cur = conn.execute(
                    "UPDATE media SET state='pending', attempts=0, next_retry_at=NULL, state_code='RETRY_REQUESTED', "
                    "state_message='수동 재시도를 요청했습니다.' WHERE state IN ('failed','error','expired')")
            return cur.rowcount


CHANNEL_EDITABLE = {
    "name", "enabled", "fetch_mode", "interval_minutes", "initial_pages", "max_pages_per_run", "category",
    "collect_media", "collect_comments", "include_notices", "recheck_days", "site", "site_channel_id",
}
CHANNEL_RUNTIME = {
    "requires_browser", "last_max_article_id", "last_run_id", "last_run_at", "last_success_at", "next_run_at",
    "last_error_code", "backfill_pages", "next_media_at",
}
