"""저장 이력과 검색 진행을 SQLite 트랜잭션으로 관리합니다. 일회성 탐색 URL은 저장하지 않습니다."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from collector import CollectorError, Target, KST, parse_target
from page_guard import SKIP_CODES
from settings import CONDITIONS


def stamp(): return datetime.now(KST).isoformat()
def packed(obj): return json.dumps(obj,ensure_ascii=False,separators=(',',':'))

def valid_article(path,cfg,expected=None):
    """파일 내부 일관성 검증. 기존 파일의 본문을 다시 추출하거나 수정하지 않습니다."""
    a=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(a,dict) or a.get('status')!='collected': raise ValueError('not collected article')
    key=(a.get('cafe_id'),a.get('article_id'))
    if key[0]!=cfg['cafe_id'] or not isinstance(key[1],str) or not re.fullmatch(r'[1-9]\d*',key[1]):
        raise ValueError('wrong identity')
    if expected and key!=expected: raise ValueError('identity changed')
    target=parse_target(a.get('url',''),cfg['cafe_id'],cfg['cafe_slug'])
    if target.article_id!=key[1]: raise ValueError('url mismatch')
    if not isinstance(a.get('title'),str) or not a['title'].strip(): raise ValueError('empty title')
    body=a.get('body')
    image_count=(a.get('media') or {}).get('image_count',0)
    image_only=a.get('content_kind')=='image_only' and type(image_count) is int and image_count>0
    if not isinstance(body,str) or (not body.strip() and not image_only) or len(body)!=a.get('body_char_count'):
        raise ValueError('body length mismatch')
    if hashlib.sha256(body.encode()).hexdigest()!=a.get('body_sha256'): raise ValueError('hash mismatch')
    date=datetime.fromisoformat(a['collected_at'])
    if date.tzinfo is None: raise ValueError('missing timezone')
    written=datetime.fromisoformat(a['written_at'])
    if written.tzinfo is None: raise ValueError('missing written timezone')
    return a


def safe_metadata(meta,keyword,article_id):
    """알려진 메타데이터만 저장하고 탐색용 일회성 URL은 제외합니다."""
    from search import validate_keyword
    if not isinstance(meta,dict): raise ValueError('invalid search metadata')
    keyword=validate_keyword(keyword)
    if meta.get('selected_article_id',article_id)!=article_id: raise ValueError('search identity mismatch')
    keys=('selected_title','scope','scope_label','period','board','sort','page','rank_on_page',
          'result_rank','selection_rule','checked_list_rows','selected_list_date_raw','selected_list_date','searched_at')
    out={k:meta[k] for k in keys if k in meta and isinstance(meta[k],(str,int,float))}
    out.update(keyword=keyword,selected_article_id=article_id)
    page=out.get('page',1)
    if type(page) is not int or page<1: page=1
    out['page']=page
    return out


class History:
    def __init__(self,cfg):
        self.cfg=cfg; path=cfg['history_db']; path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=5,isolation_level='DEFERRED')
        self.db.row_factory=sqlite3.Row
        try:
            version=self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1): raise CollectorError('HISTORY_VERSION','이력 DB 버전이 맞지 않습니다.')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS articles(
              cafe_id TEXT NOT NULL,article_id TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,
              status TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,failures INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT,last_error TEXT,file_path TEXT,body_sha256 TEXT,collected_at TEXT,
              PRIMARY KEY(cafe_id,article_id));
            CREATE TABLE IF NOT EXISTS matches(
              cafe_id TEXT NOT NULL,article_id TEXT NOT NULL,keyword TEXT NOT NULL,metadata TEXT NOT NULL,
              PRIMARY KEY(cafe_id,article_id,keyword),
              FOREIGN KEY(cafe_id,article_id) REFERENCES articles(cafe_id,article_id));
            CREATE TABLE IF NOT EXISTS scans(keyword TEXT PRIMARY KEY,state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS imported_files(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER);
            PRAGMA user_version=1;
            ''')
            identity=packed({'cafe_id':cfg['cafe_id'],'profile_dir':str(cfg['profile_dir']),'conditions':CONDITIONS})
            row=self.db.execute("SELECT value FROM settings WHERE key='identity'").fetchone()
            if row and row[0]!=identity:
                raise CollectorError('HISTORY_SCOPE_MISMATCH','이력의 카페·프로필·검색 조건과 설정이 다릅니다. 별도 history_db를 지정하세요.')
            with self.db: self.db.execute("INSERT OR IGNORE INTO settings VALUES('identity',?)",(identity,))
        except BaseException:
            self.db.close(); raise
    def close(self): self.db.close()
    def get(self,article_id):
        row=self.db.execute('SELECT * FROM articles WHERE cafe_id=? AND article_id=?',(self.cfg['cafe_id'],article_id)).fetchone()
        return dict(row) if row else None
    def match_list(self,article_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT metadata FROM matches WHERE cafe_id=? AND article_id=? ORDER BY rowid',(self.cfg['cafe_id'],article_id))]
    def _observe(self,target,title,metas,now=None):
        now=now or stamp()
        self.db.execute('''INSERT INTO articles(cafe_id,article_id,title,url,status,first_seen,last_seen)
          VALUES(?,?,?,?,'pending',?,?) ON CONFLICT(cafe_id,article_id)
          DO UPDATE SET title=excluded.title,last_seen=excluded.last_seen''',
          (target.cafe_id,target.article_id,title,target.url,now,now))
        for meta in metas:
            keyword=meta['keyword']; clean=safe_metadata(meta,keyword,target.article_id)
            from search import search_url
            clean['search_url']=search_url(target.cafe_id,keyword,clean.get('page',1))
            self.db.execute('''INSERT INTO matches VALUES(?,?,?,?) ON CONFLICT(cafe_id,article_id,keyword)
              DO UPDATE SET metadata=excluded.metadata''',(target.cafe_id,target.article_id,keyword,packed(clean)))
    def observe(self,choice):
        with self.db: self._observe(choice.target,choice.metadata['selected_title'],[choice.metadata])
    def scan(self,keyword):
        row=self.db.execute('SELECT state FROM scans WHERE keyword=?',(keyword,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_scan_page(self,keyword,state,choices):
        # 대상을 먼저 큐에 기록하고 같은 트랜잭션에서 검색 위치를 전진시킵니다.
        with self.db:
            for c in choices: self._observe(c.target,c.metadata['selected_title'],[c.metadata])
            self.db.execute('INSERT INTO scans VALUES(?,?) ON CONFLICT(keyword) DO UPDATE SET state=excluded.state',(keyword,packed(state)))
    def set_status(self,article_id,status,**fields):
        allowed={'attempts','failures','next_retry_at','last_error','file_path','body_sha256','collected_at'}
        if not set(fields)<=allowed: raise ValueError('invalid field')
        names=['status']+list(fields); values=[status]+list(fields.values())
        with self.db:
            self.db.execute('UPDATE articles SET '+','.join(k+'=?' for k in names)+' WHERE cafe_id=? AND article_id=?',values+[self.cfg['cafe_id'],article_id])
    def mark_saved(self,article,path):
        target=Target(article['cafe_id'],article['article_id'])
        with self.db:
            self._observe(target,article['title'],article.get('searches',[]),article['collected_at'])
            self.db.execute('''UPDATE articles SET status='saved',file_path=?,body_sha256=?,collected_at=?,
              failures=0,next_retry_at=NULL,last_error=NULL WHERE cafe_id=? AND article_id=?''',
              (str(Path(path).resolve()),article['body_sha256'],article['collected_at'],target.cafe_id,target.article_id))
    def all_rows(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM articles ORDER BY first_seen,CAST(article_id AS INTEGER) DESC')]
    def active_rows(self,keywords):
        active=set(keywords)
        return [r for r in self.all_rows() if active.intersection(m['keyword'] for m in self.match_list(r['article_id']))]
    def recover_and_check(self):
        report={'interrupted_recovered':0,'missing_or_invalid_files':0}
        for r in self.all_rows():
            if r['status']=='collecting':
                self.set_status(r['article_id'],'pending',last_error='INTERRUPTED')
                report['interrupted_recovered']+=1
            if r['status']=='saved':
                try:
                    a=valid_article(r['file_path'],self.cfg,(r['cafe_id'],r['article_id']))
                    if a['body_sha256']!=r['body_sha256']: raise ValueError('history hash mismatch')
                except (OSError,ValueError,KeyError,TypeError,CollectorError):
                    self.set_status(r['article_id'],'pending',last_error='RESULT_MISSING_OR_INVALID',failures=0)
                    report['missing_or_invalid_files']+=1
        return report
    def import_results(self,folders):
        report={'files_registered':0,'unchanged_files':0,'invalid_files':0,'held_registered':0,'missing_dirs':[]}
        # 정상 JSON부터 등록합니다. 파일 저장 직후 중단된 경우도 여기서 복구합니다.
        for folder in dict.fromkeys(map(Path,folders)):
            if not folder.is_dir(): report['missing_dirs'].append(str(folder)); continue
            for path in sorted(folder.rglob('*.json')):
                if path.name.startswith('summary') or not re.fullmatch(r'\d+_\d+_.+\.json',path.name): continue
                key=str(path.resolve()); info=path.stat()
                cached=self.db.execute('SELECT size,mtime FROM imported_files WHERE path=?',(key,)).fetchone()
                known=self.get(path.name.split('_')[1])
                if cached and tuple(cached)==(info.st_size,info.st_mtime_ns) and known and known['status']=='saved':
                    report['unchanged_files']+=1; continue
                try:
                    a=valid_article(path,self.cfg)
                    metas=a.get('searches',[])
                    if not metas and isinstance(a.get('search'),dict): metas=[a['search']]
                    metas=[safe_metadata(m,m['keyword'],a['article_id']) for m in metas]
                    if not metas:
                        metas=[{'keyword':k,'selected_title':a['title'],'selected_article_id':a['article_id']} for k in a.get('matched_keywords',[])]
                    old=self.get(a['article_id'])
                    with self.db:
                        self._observe(Target(a['cafe_id'],a['article_id']),a['title'],metas)
                    if not old or old['status']!='saved' or not Path(old['file_path']).is_file() or datetime.fromisoformat(a['collected_at'])>=datetime.fromisoformat(old['collected_at']):
                        self.mark_saved(a|{'searches':metas},path)
                    with self.db: self.db.execute('INSERT OR REPLACE INTO imported_files VALUES(?,?,?)',(key,info.st_size,info.st_mtime_ns))
                    report['files_registered']+=1
                except (ValueError,KeyError,TypeError,CollectorError,OSError): report['invalid_files']+=1
            # V4의 명시적 열람 제한만 가져옵니다. 원인 미확인 실패는 제한으로 추정하지 않습니다.
            for path in sorted(folder.rglob('summary*.json')):
                try:
                    s=json.loads(path.read_text(encoding='utf-8-sig'))
                    if not isinstance(s,dict): raise ValueError('invalid summary')
                    if s.get('cafe_id')!=self.cfg['cafe_id'] or not str(s.get('collector_version','')).startswith('4.'): continue
                    for item in s.get('items',[]):
                        if not isinstance(item,dict): raise ValueError('invalid summary item')
                        if item.get('status')!='skipped' or item.get('code') not in SKIP_CODES: continue
                        target=parse_target(item['url'],self.cfg['cafe_id'],self.cfg['cafe_slug'])
                        if target.article_id!=item['article_id'] or self.get(target.article_id): continue
                        metas=item.get('searches',[])
                        with self.db: self._observe(target,item['selected_title'],metas)
                        self.set_status(target.article_id,'held',last_error=item['code'])
                        report['held_registered']+=1
                except (OSError,ValueError,KeyError,TypeError,CollectorError): report['invalid_files']+=1
        return report
    def requeue(self,keywords,kind):
        count=0
        for row in self.active_rows(keywords):
            match=(kind=='held' and row['status']=='held' and row['last_error']!='ARTICLE_DELETED') or (kind=='errors' and row['status'] in ('retry','error'))
            if match:
                self.set_status(row['article_id'],'pending',failures=0,next_retry_at=None,last_error='MANUAL_RECHECK')
                count+=1
        return count
    def stats(self):
        return {r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM articles GROUP BY status')}
