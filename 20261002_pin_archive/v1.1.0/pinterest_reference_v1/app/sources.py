"""Durable, per-board state for recurring Pinterest collection."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .store import now


def board_url(value: str) -> str:
    """Accept an explicit Pinterest board, not a search or profile page."""
    from .collector import HOSTS

    value = (value or '').strip()
    try:
        parts = urlsplit(value)
        path = [part for part in parts.path.split('/') if part]
    except ValueError as exc:
        raise ValueError('Pinterest 보드 URL을 확인하세요.') from exc
    if (parts.scheme != 'https' or parts.hostname not in HOSTS or parts.username or parts.password
            or parts.port not in (None, 443) or len(path) != 2
            or path[0].lower() in ('pin', 'search', 'login', 'ideas', 'settings')):
        raise ValueError('https://www.pinterest.com/사용자/보드/ 형식의 보드 URL을 입력하세요.')
    return 'https://www.pinterest.com/' + '/'.join(path) + '/'


def after_minutes(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec='milliseconds')


class Sources:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS tracked_sources(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, target_url TEXT NOT NULL UNIQUE,
              collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
              enabled INTEGER NOT NULL, interval_minutes INTEGER NOT NULL,
              scan_limit INTEGER NOT NULL, download_limit INTEGER NOT NULL,
              next_run_at TEXT, last_run_at TEXT, last_status TEXT, last_message TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tracked_pins(
              source_id TEXT NOT NULL REFERENCES tracked_sources(id) ON DELETE CASCADE,
              pin_url TEXT NOT NULL, title TEXT NOT NULL, image_url TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', pinner TEXT NOT NULL DEFAULT '',
              source_url TEXT NOT NULL DEFAULT '',
              image_id TEXT REFERENCES pins(id) ON DELETE SET NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT, last_error TEXT,
              detail_state TEXT NOT NULL DEFAULT 'pending', detail_attempts INTEGER NOT NULL DEFAULT 0,
              detail_next_retry_at TEXT, detail_checked_at TEXT, detail_hash TEXT,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              PRIMARY KEY(source_id,pin_url)
            );
            CREATE TABLE IF NOT EXISTS tracked_pin_revisions(
              id INTEGER PRIMARY KEY,source_id TEXT NOT NULL,pin_url TEXT NOT NULL,
              detail_hash TEXT NOT NULL,snapshot_json TEXT NOT NULL,checked_at TEXT NOT NULL,
              FOREIGN KEY(source_id,pin_url) REFERENCES tracked_pins(source_id,pin_url) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS tracked_pins_state ON tracked_pins(source_id,state,next_retry_at);
            CREATE INDEX IF NOT EXISTS tracked_pins_detail ON tracked_pins(source_id,detail_state,detail_next_retry_at);
            CREATE INDEX IF NOT EXISTS tracked_sources_due ON tracked_sources(enabled,next_run_at);
            ''')
            db.execute("UPDATE tracked_sources SET last_status='interrupted',next_run_at=? "
                       "WHERE last_status IN ('running','queued')", (after_minutes(1),))

    def _decode(self, row):
        if row is None:
            return None
        result = dict(row)
        result['enabled'] = bool(result['enabled'])
        return result

    def get(self, source_id: str):
        with self.store.db() as db:
            return self._decode(db.execute('SELECT * FROM tracked_sources WHERE id=?', (source_id,)).fetchone())

    def pins(self, source_id: str, limit: int = 100):
        if self.get(source_id) is None:
            raise KeyError(source_id)
        with self.store.db() as db:
            rows = db.execute('''SELECT pin_url,title,description,pinner,source_url,image_id,state,
                attempts,next_retry_at,last_error,detail_state,detail_attempts,detail_next_retry_at,
                detail_checked_at,first_seen_at,last_seen_at FROM tracked_pins WHERE source_id=?
                ORDER BY CASE WHEN detail_state='error' OR state='error' THEN 0
                              WHEN detail_state IN ('pending','failed') OR state IN ('pending','failed') THEN 1 ELSE 2 END,
                         first_seen_at DESC LIMIT ?''',(source_id,limit)).fetchall()
        return [dict(row) for row in rows]

    def list(self):
        with self.store.db() as db:
            rows = db.execute('''SELECT s.*,
              (SELECT COUNT(*) FROM tracked_pins p WHERE p.source_id=s.id) discovered,
              (SELECT COUNT(*) FROM tracked_pins p WHERE p.source_id=s.id AND p.state='downloaded' AND p.image_id IS NOT NULL) saved,
              (SELECT COUNT(*) FROM tracked_pins p WHERE p.source_id=s.id AND (p.detail_state IN ('pending','failed') OR p.state IN ('pending','failed') OR (p.state='downloaded' AND p.image_id IS NULL))) pending,
              (SELECT COUNT(*) FROM tracked_pins p WHERE p.source_id=s.id AND (p.detail_state='error' OR p.state='error')) errors
              FROM tracked_sources s ORDER BY s.created_at''').fetchall()
        return [self._decode(row) for row in rows]

    def create(self, *, name: str, target: str, collection_id: str, interval_minutes: int,
               scan_limit: int, download_limit: int, enabled: bool):
        target = board_url(target)
        name = (name or '').strip()[:80]
        if not name:
            raise ValueError('수집할 보드 이름을 입력하세요.')
        if not 15 <= interval_minutes <= 10080 or not 1 <= scan_limit <= 1000 or not 1 <= download_limit <= 200:
            raise ValueError('실행 간격 또는 수집 한도가 범위를 벗어났습니다.')
        stamp = now()
        source_id = uuid.uuid4().hex
        with self.store.db() as db:
            if not db.execute('SELECT 1 FROM collections WHERE id=?', (collection_id,)).fetchone():
                raise ValueError('저장할 로컬 보드를 선택하세요.')
            try:
                db.execute('''INSERT INTO tracked_sources
                  (id,name,target_url,collection_id,enabled,interval_minutes,scan_limit,download_limit,
                   next_run_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                   (source_id,name,target,collection_id,int(enabled),interval_minutes,scan_limit,download_limit,
                    stamp if enabled else None,stamp,stamp))
            except Exception as exc:
                if 'UNIQUE constraint failed' in str(exc):
                    raise ValueError('이미 등록된 Pinterest 보드 URL입니다.') from exc
                raise
        return self.get(source_id)

    def update(self, source_id: str, **fields):
        allowed = {'name', 'collection_id', 'enabled', 'interval_minutes', 'scan_limit', 'download_limit'}
        if not fields or set(fields) - allowed:
            raise ValueError('지원하지 않는 변경 항목입니다.')
        source = self.get(source_id)
        if source is None:
            raise KeyError(source_id)
        merged = {**source, **fields}
        if not (merged['name'] or '').strip() or not 15 <= merged['interval_minutes'] <= 10080 or not 1 <= merged['scan_limit'] <= 1000 or not 1 <= merged['download_limit'] <= 200:
            raise ValueError('보드 설정을 확인하세요.')
        if 'name' in fields:
            fields['name'] = fields['name'].strip()[:80]
        if 'enabled' in fields:
            fields['enabled'] = int(fields['enabled'])
            fields['next_run_at'] = now() if fields['enabled'] and not source['enabled'] else (None if not fields['enabled'] else source['next_run_at'])
        elif 'interval_minutes' in fields and source['enabled']:
            fields['next_run_at'] = after_minutes(fields['interval_minutes'])
        fields['updated_at'] = now()
        with self.store.db() as db:
            if merged['collection_id'] and not db.execute('SELECT 1 FROM collections WHERE id=?', (merged['collection_id'],)).fetchone():
                raise ValueError('저장할 로컬 보드를 선택하세요.')
            db.execute('UPDATE tracked_sources SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?',
                       [*fields.values(),source_id])
        return self.get(source_id)

    def delete(self, source_id: str):
        with self.store.db() as db:
            if not db.execute('DELETE FROM tracked_sources WHERE id=?',(source_id,)).rowcount:
                raise KeyError(source_id)

    def due(self):
        with self.store.db() as db:
            return [row['id'] for row in db.execute('''SELECT id FROM tracked_sources
                WHERE enabled=1 AND next_run_at IS NOT NULL AND next_run_at<=?
                ORDER BY next_run_at LIMIT 5''',(now(),)).fetchall()]

    def mark_run(self, source_id: str, status: str, message: str = ''):
        source = self.get(source_id)
        if source is None:
            return
        stamp = now()
        next_run = after_minutes(source['interval_minutes']) if source['enabled'] else None
        with self.store.db() as db:
            db.execute('''UPDATE tracked_sources SET last_run_at=?,last_status=?,last_message=?,next_run_at=?,updated_at=?
                WHERE id=?''',(stamp,status,message[:1400],next_run,stamp,source_id))

    def mark_queued(self, source_id: str):
        with self.store.db() as db:
            db.execute("UPDATE tracked_sources SET last_status='queued',next_run_at=? WHERE id=?",
                       (after_minutes(1),source_id))

    def seen(self, source: dict, item: dict):
        stamp = now()
        with self.store.db() as db:
            existing = db.execute('SELECT image_id,state FROM tracked_pins WHERE source_id=? AND pin_url=?',
                                  (source['id'],item['pin_url'])).fetchone()
            image_id = existing['image_id'] if existing else None
            if image_id is None:
                match = db.execute('''SELECT p.id FROM pins p WHERE p.pin_url=? OR EXISTS
                    (SELECT 1 FROM sightings s WHERE s.pin_id=p.id AND s.source='pinterest' AND s.url=?)
                    ORDER BY p.width*p.height DESC LIMIT 1''',
                    (item['pin_url'],item['pin_url'])).fetchone()
                image_id = match[0] if match else None
            if existing:
                db.execute('''UPDATE tracked_pins SET title=CASE WHEN detail_state='collected' THEN title ELSE ? END,
                    image_url=CASE WHEN detail_state='collected' THEN image_url ELSE ? END,last_seen_at=?,
                    image_id=COALESCE(image_id,?),state=CASE WHEN ? IS NOT NULL THEN 'downloaded' ELSE state END
                    WHERE source_id=? AND pin_url=?''',
                    (item['title'],item['image_url'],stamp,image_id,image_id,source['id'],item['pin_url']))
            else:
                db.execute('''INSERT INTO tracked_pins(source_id,pin_url,title,image_url,image_id,state,first_seen_at,last_seen_at)
                    VALUES(?,?,?,?,?,?,?,?)''',
                    (source['id'],item['pin_url'],item['title'],item['image_url'],image_id,
                     'downloaded' if image_id else 'pending',stamp,stamp))
        if image_id and source['collection_id']:
            self.store.membership(source['collection_id'],[image_id])
        return existing is None

    def detail_candidates(self, source_id: str, limit: int, recheck_days: int = 7):
        cutoff = (datetime.now(timezone.utc)-timedelta(days=recheck_days)).isoformat(timespec='milliseconds')
        with self.store.db() as db:
            rows = db.execute('''SELECT * FROM tracked_pins WHERE source_id=? AND
                ((detail_state IN ('pending','failed') AND (detail_next_retry_at IS NULL OR detail_next_retry_at<=?))
                 OR (detail_state='collected' AND detail_checked_at<?))
                ORDER BY CASE detail_state WHEN 'collected' THEN 1 ELSE 0 END,
                         COALESCE(detail_checked_at,first_seen_at) LIMIT ?''',
                (source_id,now(),cutoff,limit)).fetchall()
        return [dict(row) for row in rows]

    def record_detail(self, source_id: str, pin_url: str, detail: dict):
        snapshot = json.dumps(detail,ensure_ascii=False,sort_keys=True)
        digest = hashlib.sha256(snapshot.encode('utf-8')).hexdigest()
        stamp = now()
        with self.store.db() as db:
            old = db.execute('SELECT detail_hash FROM tracked_pins WHERE source_id=? AND pin_url=?',
                             (source_id,pin_url)).fetchone()
            if old is None:
                raise KeyError(pin_url)
            if old['detail_hash'] != digest:
                db.execute('''INSERT INTO tracked_pin_revisions(source_id,pin_url,detail_hash,snapshot_json,checked_at)
                    VALUES(?,?,?,?,?)''',(source_id,pin_url,digest,snapshot,stamp))
            db.execute('''UPDATE tracked_pins SET title=?,description=?,pinner=?,source_url=?,image_url=?,
                detail_state='collected',detail_attempts=0,detail_next_retry_at=NULL,
                detail_checked_at=?,detail_hash=? WHERE source_id=? AND pin_url=?''',
                (detail['title'],detail['description'],detail['pinner'],detail['source_url'],
                 detail['image_url'],stamp,digest,source_id,pin_url))
        return old['detail_hash'] != digest

    def detail_failed(self, source_id: str, pin_url: str, message: str):
        with self.store.db() as db:
            row = db.execute('SELECT detail_attempts FROM tracked_pins WHERE source_id=? AND pin_url=?',
                             (source_id,pin_url)).fetchone()
            if row is None:
                return
            attempts = row['detail_attempts']+1
            db.execute('''UPDATE tracked_pins SET detail_state=?,detail_attempts=?,detail_next_retry_at=?,
                last_error=? WHERE source_id=? AND pin_url=?''',
                ('error' if attempts >= 4 else 'failed',attempts,
                 None if attempts >= 4 else after_minutes(min(10*2**(attempts-1),360)),
                 message[:500],source_id,pin_url))

    def pending(self, source_id: str, limit: int):
        with self.store.db() as db:
            rows = db.execute('''SELECT * FROM tracked_pins WHERE source_id=? AND detail_state='collected' AND
                ((state IN ('pending','failed') AND (next_retry_at IS NULL OR next_retry_at<=?))
                OR (state='downloaded' AND image_id IS NULL))
                ORDER BY CASE state WHEN 'failed' THEN 0 ELSE 1 END,first_seen_at LIMIT ?''',
                (source_id,now(),limit)).fetchall()
        return [dict(row) for row in rows]

    def succeeded(self, source_id: str, pin_url: str, image_id: str):
        with self.store.db() as db:
            db.execute('''UPDATE tracked_pins SET image_id=?,state='downloaded',attempts=0,
                next_retry_at=NULL,last_error=NULL WHERE source_id=? AND pin_url=?''',
                (image_id,source_id,pin_url))

    def failed(self, source_id: str, pin_url: str, message: str):
        with self.store.db() as db:
            row = db.execute('SELECT attempts FROM tracked_pins WHERE source_id=? AND pin_url=?',
                             (source_id,pin_url)).fetchone()
            if row is None:
                return
            attempts = row['attempts']+1
            delay = min(10 * 2**(attempts-1),360)
            db.execute('''UPDATE tracked_pins SET state=?,attempts=?,next_retry_at=?,last_error=?
                WHERE source_id=? AND pin_url=?''',
                ('error' if attempts >= 4 else 'failed',attempts,
                 None if attempts >= 4 else after_minutes(delay),message[:500],source_id,pin_url))

    def retry_errors(self, source_id: str):
        if self.get(source_id) is None:
            raise KeyError(source_id)
        with self.store.db() as db:
            media = db.execute('''UPDATE tracked_pins SET state='pending',attempts=0,next_retry_at=NULL,
                last_error=NULL WHERE source_id=? AND state='error' ''',(source_id,)).rowcount
            details = db.execute('''UPDATE tracked_pins SET detail_state='pending',detail_attempts=0,
                detail_next_retry_at=NULL,last_error=NULL WHERE source_id=? AND detail_state='error' ''',
                (source_id,)).rowcount
        return media+details
