"""파일을 이동하거나 수정하지 않고 V1~V8 결과를 SQLite에 등록합니다."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

BASE = Path(__file__).resolve().parents[1]
VIDEO = {'.mp4', '.webm', '.mkv', '.mov', '.m4v'}
AUDIO = {'.m4a', '.mp3', '.wav', '.flac', '.opus', '.ogg', '.aac'}
IMAGES = {'.jpg', '.jpeg', '.png', '.webp'}
SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', '.work', 'data'}
TIME_RE = re.compile(r'^\[(\d{1,3}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?\]\s*(.*)$')


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_text(path: Path, limit: int = 2_000_000) -> str:
    with path.open('rb') as stream:
        raw = stream.read(limit)
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        if len(raw) == limit and exc.start >= len(raw) - 4:
            return raw.decode('utf-8-sig', errors='replace')
        return raw.decode('cp949', errors='replace')


def safe_json(path: Path) -> dict:
    try:
        if path.stat().st_size > 8_000_000:
            return {}
        obj = json.loads(read_text(path, 8_000_000))
        return obj if isinstance(obj, dict) else {}
    except (ValueError, OSError):
        return {}


def atomic_text(path: Path, text: str, encoding: str = 'utf-8') -> None:
    """완성되지 않은 내용을 기존 파일에 덮어쓰지 않도록 임시 파일을 교체합니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def classify(path: Path) -> tuple[str, str] | None:
    """폴더 내 실제 파일만 분류하며 metadata의 임의 경로는 신뢰하지 않습니다."""
    name, ext = path.stem, path.suffix.lower()
    if path.is_symlink() or re.search(r'\.f\d+(?:\.|$)', path.name):
        return None
    if '__audio_source' in name or '_batch_result' in name or '__rawsub' in name:
        return None
    if path.name == 'transcript.txt':
        return path.parent.name, 'transcript'
    if path.name == 'transcript_timestamp.txt':
        return path.parent.name, 'timestamp'
    suffixes = [('_mobile720', 'mobile'), ('_mobile480', 'economy'), ('_browser_preview', 'preview'), ('_metadata', 'metadata'),
                ('_segments', 'segments'), ('_transcript', 'transcript'),
                ('_timestamp', 'timestamp'), ('_thumbnail', 'thumbnail')]
    for suffix, kind in suffixes:
        if name.endswith(suffix):
            valid = (kind in {'preview','mobile','economy'} and ext == '.mp4') or (kind in {'metadata', 'segments'} and ext == '.json') or (kind in {'transcript', 'timestamp'} and ext == '.txt') or (kind == 'thumbnail' and ext in IMAGES)
            if valid:
                return name[:-len(suffix)], kind
    if ext in VIDEO:
        return name, 'video'
    if ext in AUDIO:
        return name, 'audio'
    if ext in IMAGES:
        return None  # 썸네일 접미사 없는 일반 이미지는 수집하지 않음
    if ext == '.txt' and re.match(r'^\d{8}_', name):
        return name, 'transcript'
    return None


def mime(path: Path, kind: str) -> str:
    fixed = {'.m4a': 'audio/mp4', '.mp4': 'video/mp4', '.mkv': 'video/x-matroska',
             '.opus': 'audio/ogg', '.flac': 'audio/flac', '.wav': 'audio/wav',
             '.txt': 'text/plain', '.json': 'application/json'}
    return fixed.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream')


class Store:
    def __init__(self, data_dir: Path | str | None = None):
        self.data_dir = Path(data_dir or os.environ.get('YME_DATA_DIR', BASE / 'data')).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = self.data_dir / 'library.sqlite3'
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY, folder TEXT NOT NULL, stem TEXT NOT NULL,
              video_id TEXT DEFAULT '', title TEXT NOT NULL, channel TEXT DEFAULT '',
              source_url TEXT DEFAULT '', added_at TEXT NOT NULL,
              metadata TEXT DEFAULT '{}', search_text TEXT DEFAULT '', favorite INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY, item_id TEXT REFERENCES items(id) ON DELETE CASCADE,
              kind TEXT NOT NULL, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
              size INTEGER NOT NULL, mime TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS files_item ON files(item_id);
            CREATE INDEX IF NOT EXISTS items_added ON items(added_at DESC,id);
            CREATE TABLE IF NOT EXISTS derivatives (
              file_id TEXT PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
              source_id TEXT NOT NULL, source_size INTEGER NOT NULL,
              source_mtime_ns INTEGER NOT NULL, profile TEXT NOT NULL);

            CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL, key TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS item_tags (
              item_id TEXT REFERENCES items(id) ON DELETE CASCADE,
              tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
              PRIMARY KEY(item_id, tag_id));
            CREATE TABLE IF NOT EXISTS roots (path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
              status TEXT NOT NULL, progress REAL DEFAULT 0, stage TEXT DEFAULT '',
              logs TEXT DEFAULT '', result TEXT DEFAULT '{}',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def item(self, item_id: str) -> dict | None:
        with self.connect() as c:
            r = c.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
            return self._expand(c, r) if r else None

    def _expand(self, c, r) -> dict:
        item = dict(r)
        item.pop('search_text', None)
        item['metadata'] = json.loads(item['metadata'])
        item['tags'] = [dict(t) for t in c.execute('SELECT t.id,t.name FROM tags t JOIN item_tags it ON t.id=it.tag_id WHERE it.item_id=? ORDER BY t.name', (item['id'],))]
        item['files'] = [dict(f) for f in c.execute('SELECT id,kind,name,size,mime FROM files WHERE item_id=? ORDER BY kind,name', (item['id'],))]
        return item

    def listing(self, q: str = '', kind: str = '', tags: list[int] | None = None,
                favorite: bool = False, untagged: bool = False, offset: int = 0,
                limit: int = 200, sort: str = 'new', compact: bool = False) -> dict:
        clauses, args = [], []
        if q:
            clauses.append("(title LIKE ? ESCAPE '\\' OR channel LIKE ? ESCAPE '\\' OR search_text LIKE ? ESCAPE '\\')")
            q = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            args += [q] * 3
        if kind in {'video', 'audio', 'transcript'}:
            clauses.append('EXISTS(SELECT 1 FROM files f WHERE f.item_id=items.id AND f.kind=?)')
            args.append(kind)
        if favorite:
            clauses.append('favorite=1')
        if untagged:
            clauses.append('NOT EXISTS(SELECT 1 FROM item_tags it WHERE it.item_id=items.id)')
        for tag in tags or []:
            clauses.append('EXISTS(SELECT 1 FROM item_tags it WHERE it.item_id=items.id AND it.tag_id=?)')
            args.append(tag)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        order = {'new': 'added_at DESC,id', 'old': 'added_at,id', 'title': 'title COLLATE NOCASE,id'}.get(sort, 'added_at DESC,id')
        with self.connect() as c:
            total = c.execute('SELECT count(*) FROM items' + where, args).fetchone()[0]
            if not compact:
                rows = c.execute('SELECT * FROM items' + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [limit, offset]).fetchall()
                return {'items': [self._expand(c, r) for r in rows], 'total': total}
            columns = ('id,video_id,title,channel,source_url,added_at,favorite,'
                       "CASE WHEN json_valid(metadata) THEN json_extract(metadata,'$.duration_seconds') END AS duration_seconds")
            rows = [dict(r) for r in c.execute('SELECT ' + columns + ' FROM items' + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', args + [limit, offset])]
            by_id = {r['id']: r for r in rows}
            for row in rows:
                row['metadata'] = {'duration_seconds': row.pop('duration_seconds')}
                row['files'], row['tags'] = [], []
            if rows:
                marks = ','.join('?' for _ in rows)
                keys = list(by_id)
                for f in c.execute(f'SELECT item_id,id,kind,name,size,mime FROM files WHERE item_id IN ({marks}) ORDER BY kind,name', keys):
                    f = dict(f); by_id[f.pop('item_id')]['files'].append(f)
                for t in c.execute(f'SELECT it.item_id,t.id,t.name FROM tags t JOIN item_tags it ON t.id=it.tag_id WHERE it.item_id IN ({marks}) ORDER BY t.name', keys):
                    t = dict(t); by_id[t.pop('item_id')]['tags'].append(t)
            return {'items': rows, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + len(rows) < total}

    def file(self, file_id: str) -> dict | None:
        with self.connect() as c:
            r = c.execute('SELECT * FROM files WHERE id=?', (file_id,)).fetchone()
            if not r:
                return None
            value = dict(r)
            p = Path(value['path'])
            # 심볼릭 링크로 바꾼 파일은 제공하지 않습니다.
            if p.is_symlink() or not p.is_file() or str(p.resolve()) != str(p):
                return None
            if value['kind'] in {'mobile', 'economy'}:
                from .performance import valid_derivative
                if not valid_derivative(self, file_id):
                    return None
            return value

    def tags(self) -> list[dict]:
        with self.connect() as c:
            return [dict(t) for t in c.execute('SELECT t.id,t.name,count(it.item_id) AS count FROM tags t LEFT JOIN item_tags it ON t.id=it.tag_id GROUP BY t.id ORDER BY t.name COLLATE NOCASE')]

    @staticmethod
    def tag_name(name: str) -> tuple[str, str]:
        name = unicodedata.normalize('NFKC', str(name)).strip().lstrip('#').strip()
        if not name or len(name) > 40 or any(ord(ch) < 32 for ch in name):
            raise ValueError('태그는 1~40자의 일반 문자로 입력해 주세요.')
        return name, name.casefold()

    def create_tag(self, name: str) -> int:
        name, key = self.tag_name(name)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO tags(name,key) VALUES (?,?)', (name, key))
            return c.execute('SELECT id FROM tags WHERE key=?', (key,)).fetchone()[0]

    def rename_tag(self, tag_id: int, name: str) -> None:
        name, key = self.tag_name(name)
        with self.connect() as c:
            try:
                updated = c.execute('UPDATE tags SET name=?,key=? WHERE id=?', (name, key, tag_id))
            except sqlite3.IntegrityError:
                raise ValueError('이미 같은 이름의 태그가 있습니다.') from None
            if not updated.rowcount:
                raise ValueError('태그를 찾지 못했습니다.')

    def delete_tag(self, tag_id: int) -> None:
        with self.connect() as c:
            if not c.execute('DELETE FROM tags WHERE id=?', (tag_id,)).rowcount:
                raise ValueError('태그를 찾지 못했습니다.')

    def assign_tags(self, item_id: str, names: list[str], replace: bool = False) -> None:
        normalized = {}
        for raw in names:
            if not raw.strip():
                continue
            name, key = self.tag_name(raw)
            normalized.setdefault(key, name)
        with self.connect() as c:
            if not c.execute('SELECT 1 FROM items WHERE id=?', (item_id,)).fetchone():
                raise ValueError('항목을 찾지 못했습니다.')
            ids = []
            for key, name in normalized.items():
                c.execute('INSERT OR IGNORE INTO tags(name,key) VALUES (?,?)', (name, key))
                ids.append(c.execute('SELECT id FROM tags WHERE key=?', (key,)).fetchone()[0])
            if replace:
                c.execute('DELETE FROM item_tags WHERE item_id=?', (item_id,))
            for tag in ids:
                c.execute('INSERT OR IGNORE INTO item_tags VALUES (?,?)', (item_id, tag))

    def set_favorite(self, item_id: str, value: bool) -> None:
        with self.connect() as c:
            c.execute('UPDATE items SET favorite=? WHERE id=?', (int(value), item_id))

    def roots(self) -> list[str]:
        with self.connect() as c:
            return [r[0] for r in c.execute('SELECT path FROM roots ORDER BY path')]

    def register_folder(self, folder: Path, tags: list[str] | None = None) -> list[str]:
        folder = folder.resolve()
        groups: dict[str, dict[str, Path]] = {}
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            classified = classify(path)
            if classified and path.stat().st_size > 0:
                stem, kind = classified
                groups.setdefault(stem, {})[kind] = path
        ids = []
        for stem, files in groups.items():
            if not set(files) & {'video', 'audio', 'transcript', 'timestamp', 'metadata'}:
                continue
            meta = safe_json(files['metadata']) if 'metadata' in files else {}
            text = ''
            for kind in ('transcript', 'timestamp'):
                if kind in files:
                    text = read_text(files[kind])
                    break
            header = {}
            for line in text.splitlines()[:30]:
                if ':' in line:
                    k, v = line.split(':', 1)
                    header[k.strip()] = v.strip()
            raw_id = str(meta.get('video_id') or header.get('Video ID') or '')
            video_id = raw_id if re.fullmatch(r'[\w-]{11}', raw_id, flags=re.ASCII) else ''
            title = str(meta.get('title') or header.get('유튜브 제목') or re.sub(r'^\d{8}_', '', stem))
            channel = str(meta.get('channel') or header.get('채널명') or '')
            date = str(meta.get('saved_at') or meta.get('downloaded_at') or '')
            if not date:
                date = (header.get('가져온 날짜', '') + 'T' + header.get('가져온 시간', '')).strip('T')
            if not date:
                date = datetime.fromtimestamp(max(p.stat().st_mtime for p in files.values())).astimezone().isoformat(timespec='seconds')
            identity = str(folder) + '\0' + stem
            item_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
            with self.connect() as c:
                c.execute('''INSERT INTO items(id,folder,stem,video_id,title,channel,source_url,added_at,metadata,search_text)
                 VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                 video_id=excluded.video_id,title=excluded.title,channel=excluded.channel,source_url=excluded.source_url,
                 metadata=excluded.metadata,search_text=excluded.search_text''',
                 (item_id, str(folder), stem, video_id, title, channel,
                  f'https://www.youtube.com/watch?v={video_id}' if video_id else '', date,
                  json.dumps(meta, ensure_ascii=False), text))
                c.execute("DELETE FROM files WHERE item_id=? AND kind NOT IN ('preview','mobile','economy')", (item_id,))
                for kind, path in files.items():
                    path = path.resolve()
                    fid = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                    c.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)',
                              (fid, item_id, kind, str(path), path.name, path.stat().st_size, mime(path, kind)))
            self.assign_tags(item_id, tags or [])
            ids.append(item_id)
        return ids

    def scan(self, root: Path, tags: list[str] | None = None, notify=None) -> dict:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError('존재하는 결과 폴더를 선택해 주세요.')
        count, visited, errors = 0, 0, []
        for directory, dirs, names in os.walk(root, followlinks=False):
            current = Path(directory)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.') and not (current / d).is_symlink()]
            if len(current.relative_to(root).parts) >= 10:
                dirs[:] = []
            visited += 1
            if visited > 20000:
                errors.append('폴더 20,000개 검색 제한에 도달했습니다. 결과 폴더를 더 좁혀 주세요.')
                break
            try:
                count += len(self.register_folder(current, tags))
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(f'{current.name}: {exc}')
            if notify and visited % 30 == 0:
                notify(f'{visited}개 폴더 확인 · {count}개 항목 등록')
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO roots VALUES (?)', (str(root),))
        return {'count': count, 'errors': errors[:100], 'root': str(root)}

    def job_summaries(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("""SELECT id,kind,status,progress,stage,created_at,updated_at,
                CASE WHEN json_valid(payload) THEN substr(COALESCE(json_extract(payload,'$.text'),json_extract(payload,'$.path'),''),1,180) ELSE '' END AS input_hint,
                CASE WHEN json_valid(payload) THEN json_extract(payload,'$.item_id') END AS item_id,
                CASE WHEN json_valid(payload) THEN json_extract(payload,'$.profile') END AS profile
                FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 80""").fetchall()
        return [dict(r) for r in rows]

    def job(self, ident: str) -> dict | None:
        with self.connect() as c:
            row = c.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
        if not row:
            return None
        row = dict(row)
        row['payload'] = json.loads(row['payload'])
        row['result'] = json.loads(row['result'])
        return row

    def jobs(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute('SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 80').fetchall()
        result = []
        for r in rows:
            r = dict(r)
            r['payload'] = json.loads(r['payload'])
            r['result'] = json.loads(r['result'])
            result.append(r)
        return result

    def job_update(self, job_id: str, **values) -> None:
        allowed = {'status', 'progress', 'stage', 'logs', 'result'}
        values = {k: v for k, v in values.items() if k in allowed}
        values['updated_at'] = now()
        if 'result' in values and not isinstance(values['result'], str):
            values['result'] = json.dumps(values['result'], ensure_ascii=False)
        with self.connect() as c:
            c.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])
