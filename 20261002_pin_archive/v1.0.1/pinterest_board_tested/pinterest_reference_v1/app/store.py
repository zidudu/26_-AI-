"""SQLite storage, image validation, exact deduplication and lexical retrieval.

Each operation uses a short-lived connection. Images keep original bytes; only
thumbnails are transformed. AI annotations never overwrite manual metadata.
"""
from __future__ import annotations
import hashlib
import io
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image, ImageOps, UnidentifiedImageError

from . import __version__ as VERSION
MAX_IMAGE_BYTES = 20 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')

def tags(value):
    if isinstance(value, str): value = re.split(r'[,\n]', value)
    if not isinstance(value, (list, tuple)): value = []
    return list(dict.fromkeys(str(t).strip()[:80] for t in value if isinstance(t, str) and t.strip()))[:80]

def link(value):
    value = str(value or '').strip()[:4096]
    try:
        u = urlsplit(value)
        if u.scheme in ('https','http') and u.hostname and not u.username and not u.password and not any(c in value for c in '\r\n\0'): return value
    except ValueError: pass
    return ''

class Store:
    def __init__(self, root: Path):
        self.root = root.resolve(); self.root.mkdir(parents=True, exist_ok=True)
        self.images = self.root/'images'; self.thumbs = self.root/'thumbnails'
        self.images.mkdir(exist_ok=True); self.thumbs.mkdir(exist_ok=True)
        self.path = self.root/'library.sqlite3'; self.lock = threading.RLock()
        with self.db() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS pins(
                id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,pixel_hash TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,byte_size INTEGER NOT NULL,
                dhash TEXT NOT NULL,palette TEXT NOT NULL, title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '미분류',tags TEXT NOT NULL DEFAULT '[]',technical_tags TEXT NOT NULL DEFAULT '[]',
                ai_tags TEXT NOT NULL DEFAULT '[]',ai_description TEXT NOT NULL DEFAULT '',ai_result TEXT NOT NULL DEFAULT '{}',
                ai_status TEXT NOT NULL DEFAULT 'pending',source TEXT NOT NULL DEFAULT 'local',
                pin_url TEXT NOT NULL DEFAULT '',image_url TEXT NOT NULL DEFAULT '',source_url TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',crawl_keywords TEXT NOT NULL DEFAULT '[]',license_note TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS collection_pins(collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
                pin_id TEXT REFERENCES pins(id) ON DELETE CASCADE,PRIMARY KEY(collection_id,pin_id));
            CREATE TABLE IF NOT EXISTS sightings(id INTEGER PRIMARY KEY,pin_id TEXT REFERENCES pins(id) ON DELETE CASCADE,
                source TEXT NOT NULL,url TEXT NOT NULL,keyword TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(pin_id,source,url,keyword));
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS vectors(pin_id TEXT REFERENCES pins(id) ON DELETE CASCADE,model TEXT NOT NULL,
                embedding BLOB NOT NULL,dimensions INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(pin_id,model));
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,
                progress INTEGER DEFAULT 0,total INTEGER DEFAULT 0,added INTEGER DEFAULT 0,duplicates INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,message TEXT DEFAULT '',logs TEXT DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pins_created ON pins(created_at);
            CREATE INDEX IF NOT EXISTS pins_source ON pins(source);
            CREATE VIRTUAL TABLE IF NOT EXISTS pins_fts USING fts5(pin_id UNINDEXED,title,tags,body,tokenize='unicode61');
            ''')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=30000')
        try: yield c; c.commit()
        except Exception: c.rollback(); raise
        finally: c.close()

    def _index(self,c,pid):
        c.execute('DELETE FROM pins_fts WHERE pin_id=?',(pid,))
        r=c.execute('SELECT * FROM pins WHERE id=?',(pid,)).fetchone()
        if r:
            all_tags=json.loads(r['tags'])+json.loads(r['technical_tags'])+json.loads(r['ai_tags'])
            body=' '.join([r['description'],r['ai_description'],r['category'],r['author'],' '.join(json.loads(r['crawl_keywords']))])
            c.execute('INSERT INTO pins_fts VALUES(?,?,?,?)',(pid,r['title'],' '.join(all_tags),body))

    def decode(self,row):
        if row is None: return None
        p=dict(row)
        for k in ('tags','technical_tags','ai_tags','palette','ai_result','crawl_keywords'): p[k]=json.loads(p[k])
        p['favorite']=bool(p['favorite']); p['all_tags']=tags(p['tags']+p['ai_tags']+p['technical_tags'])
        p['image']=f'/media/{p["id"]}/image'; p['thumbnail']=f'/media/{p["id"]}/thumbnail'
        return p

    def get(self,pid):
        with self.db() as c:
            p=self.decode(c.execute('SELECT * FROM pins WHERE id=?',(pid,)).fetchone())
            if p:
                p['collections']=[dict(r) for r in c.execute('SELECT c.id,c.name FROM collections c JOIN collection_pins cp ON cp.collection_id=c.id WHERE cp.pin_id=?',(pid,))]
                p['sightings']=[dict(r) for r in c.execute('SELECT source,url,keyword,created_at FROM sightings WHERE pin_id=? ORDER BY id DESC LIMIT 100',(pid,))]
            return p

    def add_image(self,raw:bytes,metadata:dict|None=None):
        m=metadata or {}
        if not raw or len(raw)>MAX_IMAGE_BYTES: raise ValueError('파일당 20 MB 이하의 이미지를 선택하세요.')
        try:
            with Image.open(io.BytesIO(raw)) as im:
                fmt=im.format
                if fmt not in ('JPEG','PNG','WEBP','GIF','BMP'): raise ValueError('JPEG, PNG, WEBP, GIF, BMP만 지원합니다.')
                if im.width*im.height>40_000_000 or min(im.size)<8: raise ValueError('8×8 이상, 40메가픽셀 이하 이미지만 지원합니다.')
                animated=getattr(im,'n_frames',1)>1
                oriented=ImageOps.exif_transpose(im)
                rgba=oriented.convert('RGBA'); rgba.load()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as e:
            raise ValueError('손상되었거나 지원하지 않는 이미지입니다.') from e
        w,h=rgba.size; digest=hashlib.sha256(raw).hexdigest()
        # Preserve alpha, and never merge animations solely by their first frame.
        pixels=hashlib.sha256(f'{w},{h},RGBA,'.encode()+rgba.tobytes()).hexdigest() if not animated else 'animated:'+digest
        background=Image.new('RGBA',(w,h),'white'); background.alpha_composite(rgba); rgb=background.convert('RGB')
        gray=rgb.resize((9,8)).convert('L'); grid=list(gray.get_flattened_data())
        dhash=f'{sum(int(grid[y*9+x]>grid[y*9+x+1]) << (y*8+x) for y in range(8) for x in range(8)):016x}'
        quant=rgb.resize((80,80)).quantize(colors=5); pal=quant.getpalette()
        palette=['#%02x%02x%02x'%tuple(pal[i*3:i*3+3]) for _,i in sorted(quant.getcolors(),reverse=True)]
        technical=['세로','portrait'] if h>w*1.15 else ['가로','landscape'] if w>h*1.15 else ['정사각형','square']
        if animated: technical+=['애니메이션','animated']
        source=m.get('source','local') if m.get('source','local') in ('local','import','pinterest','demo','url') else 'import'
        keywords=tags(m.get('crawl_keywords',m.get('crawl_keyword',''))); stamp=now(); created_files=[]
        with self.lock:
            try:
                with self.db() as c:
                    old=c.execute('SELECT * FROM pins WHERE sha256=? OR pixel_hash=?',(digest,pixels)).fetchone()
                    fresh=old is None
                    if old:
                        pid=old['id']
                        values={'crawl_keywords':json.dumps(tags(json.loads(old['crawl_keywords'])+keywords),ensure_ascii=False),
                                'tags':json.dumps(tags(json.loads(old['tags'])+tags(m.get('tags'))),ensure_ascii=False),'updated_at':stamp}
                        for k in ('pin_url','image_url','source_url'):
                            incoming=link(m.get(k,m.get('sourceUrl') if k=='source_url' else ''))
                            if not old[k] and incoming: values[k]=incoming
                        c.execute('UPDATE pins SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[pid])
                    else:
                        pid=uuid.uuid4().hex; ext={'JPEG':'jpg','PNG':'png','WEBP':'webp','GIF':'gif','BMP':'bmp'}[fmt]
                        filename=pid+'.'+ext; orig=self.images/filename; thumb=self.thumbs/(pid+'.webp')
                        created_files=[orig,thumb]; orig.write_bytes(raw)
                        rgb.thumbnail((700,1000)); rgb.save(thumb,'WEBP',quality=84)
                        values=dict(id=pid,sha256=digest,pixel_hash=pixels,filename=filename,width=w,height=h,byte_size=len(raw),dhash=dhash,
                            palette=json.dumps(palette),title=str(m.get('title') or '제목 없는 이미지')[:300],
                            description=str(m.get('description') or '')[:6000],category=str(m.get('category') or '미분류')[:80],
                            tags=json.dumps(tags(m.get('tags')),ensure_ascii=False),technical_tags=json.dumps(technical,ensure_ascii=False),
                            source=source,pin_url=link(m.get('pin_url')),image_url=link(m.get('image_url')),source_url=link(m.get('source_url',m.get('sourceUrl'))),
                            author=str(m.get('author') or '')[:160],crawl_keywords=json.dumps(keywords,ensure_ascii=False),
                            license_note=str(m.get('license_note') or '권리 미확인 · 원본 출처와 이용 조건을 확인하세요.')[:2000],created_at=stamp,updated_at=stamp)
                        c.execute('INSERT INTO pins('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
                    cid=m.get('collection_id')
                    if cid:
                        if not c.execute('SELECT 1 FROM collections WHERE id=?',(cid,)).fetchone(): raise ValueError('보드를 찾을 수 없습니다.')
                        c.execute('INSERT OR IGNORE INTO collection_pins VALUES(?,?)',(cid,pid))
                    url=link(m.get('pin_url') or m.get('source_url') or m.get('sourceUrl') or m.get('image_url'))
                    for keyword in keywords or ['']:
                        c.execute('INSERT OR IGNORE INTO sightings(pin_id,source,url,keyword,created_at) VALUES(?,?,?,?,?)',(pid,source,url,keyword,stamp))
                    self._index(c,pid)
            except Exception:
                for p in created_files: p.unlink(missing_ok=True)
                raise
        return self.get(pid),fresh

    def update(self,pid,fields):
        values={}
        for k,v in fields.items():
            if v is None: continue
            if k=='tags': values[k]=json.dumps(tags(v),ensure_ascii=False)
            elif k=='favorite': values[k]=int(bool(v))
            elif k=='source_url': values[k]=link(v)
            elif k in {'title','description','category','license_note','author'}:
                values[k]=str(v)[:{'title':300,'description':6000,'category':80,'license_note':2000,'author':160}[k]]
        if not self.get(pid): raise KeyError(pid)
        if values:
            values['updated_at']=now()
            with self.db() as c:
                c.execute('UPDATE pins SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[pid]); self._index(c,pid)
        return self.get(pid)

    def save_analysis(self,pid,result,provider,model):
        data={**result,'provider':provider,'model':model,'analyzed_at':now(),'needs_review':True}
        with self.db() as c:
            r=c.execute('UPDATE pins SET ai_tags=?,ai_description=?,ai_result=?,ai_status=?,updated_at=? WHERE id=?',
                (json.dumps(tags(result.get('tags')),ensure_ascii=False),str(result.get('description',''))[:6000],json.dumps(data,ensure_ascii=False),'done',now(),pid))
            if not r.rowcount: raise KeyError(pid)
            self._index(c,pid)

    def delete(self,pid):
        with self.lock:
            p=self.get(pid)
            if not p: raise KeyError(pid)
            with self.db() as c:
                c.execute('DELETE FROM pins_fts WHERE pin_id=?',(pid,)); c.execute('DELETE FROM pins WHERE id=?',(pid,))
            (self.images/p['filename']).unlink(missing_ok=True); (self.thumbs/(pid+'.webp')).unlink(missing_ok=True)

    def candidates(self,category='',source='',favorite=False,collection='',untagged=False):
        where=[]; args=[]
        for field,value in (('category',category),('source',source)):
            if value: where.append(field+'=?'); args.append(value)
        if favorite: where.append('favorite=1')
        if untagged: where.append("ai_status!='done'")
        if collection: where.append('id IN (SELECT pin_id FROM collection_pins WHERE collection_id=?)'); args.append(collection)
        with self.db() as c: return [self.decode(r) for r in c.execute('SELECT * FROM pins'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY created_at DESC,id',args)]

    def keyword_ranks(self,query,candidates):
        tokens=re.findall(r'\w+',query.casefold())[:20]
        if not tokens: return [(p,0.) for p in candidates] if not query.strip() else []
        expression=' OR '.join('"'+t.replace('"','""')+'"*' for t in tokens)
        with self.db() as c: boost={r[0]:1/(i+1) for i,r in enumerate(c.execute('SELECT pin_id FROM pins_fts WHERE pins_fts MATCH ? ORDER BY bm25(pins_fts,0,5,4,1)',(expression,)))}
        ranked=[]
        for p in candidates:
            title=p['title'].casefold(); keywords=' '.join(p['all_tags']+[p['category']]+p['crawl_keywords']).casefold()
            body=' '.join([p['description'],p['ai_description'],p['author']]).casefold()
            score=sum(6*(t in title)+4*(t in keywords)+(t in body) for t in tokens)+8*(query.casefold() in title)+boost.get(p['id'],0)
            if score: ranked.append((p,float(score)))
        return sorted(ranked,key=lambda x:x[1],reverse=True)

    def collections(self):
        with self.db() as c: return [dict(r) for r in c.execute('SELECT c.*,COUNT(cp.pin_id) count FROM collections c LEFT JOIN collection_pins cp ON cp.collection_id=c.id GROUP BY c.id ORDER BY c.created_at')]

    def create_collection(self,name):
        name=name.strip()[:80]
        if not name: raise ValueError('보드 이름을 입력하세요.')
        cid=uuid.uuid4().hex
        with self.db() as c:
            try: c.execute('INSERT INTO collections VALUES(?,?,?)',(cid,name,now()))
            except sqlite3.IntegrityError as e: raise ValueError('같은 이름의 보드가 있습니다.') from e
        return {'id':cid,'name':name,'count':0}

    def membership(self,cid,ids,add=True):
        with self.db() as c:
            if not c.execute('SELECT 1 FROM collections WHERE id=?',(cid,)).fetchone(): raise KeyError(cid)
            for pid in ids:
                if not c.execute('SELECT 1 FROM pins WHERE id=?',(pid,)).fetchone(): raise ValueError('선택 이미지가 삭제되었거나 없습니다.')
                if add: c.execute('INSERT OR IGNORE INTO collection_pins VALUES(?,?)',(cid,pid))
                else: c.execute('DELETE FROM collection_pins WHERE collection_id=? AND pin_id=?',(cid,pid))

    def stats(self):
        with self.db() as c:
            s=dict(c.execute("SELECT COUNT(*) total,COALESCE(SUM(favorite),0) favorites,COALESCE(SUM(ai_status='done'),0) analyzed,COALESCE(SUM(byte_size),0) bytes FROM pins").fetchone())
            s['indexed']=c.execute('SELECT COUNT(DISTINCT pin_id) FROM vectors').fetchone()[0]
            s['categories']=[dict(r) for r in c.execute('SELECT category name,COUNT(*) count FROM pins GROUP BY category ORDER BY count DESC')]
            s['sources']=[dict(r) for r in c.execute('SELECT source name,COUNT(*) count FROM pins GROUP BY source')]
        s['collections']=self.collections(); return s

    def settings(self):
        result={'provider':'none','openai_model':'gpt-4.1-mini','ollama_model':'','browser_channel':'chromium','seed_loaded':False}
        with self.db() as c:
            for r in c.execute('SELECT key,value FROM settings'):
                if r['key'] in result: result[r['key']]=json.loads(r['value'])
        return result

    def save_settings(self,values):
        allowed=set(self.settings())
        with self.db() as c:
            for k,v in values.items():
                if k in allowed: c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))

    def seed(self,folder):
        if self.settings()['seed_loaded']: return
        for p in json.loads((folder/'manifest.json').read_text(encoding='utf-8')):
            self.add_image((folder/p['image']).read_bytes(),{**p,'source':'demo','license_note':p.get('license_note') or '사용자가 제공한 갤러리의 샘플입니다. 원본 출처와 이용 조건을 확인하세요.'})
        self.save_settings({'seed_loaded':True})

    def job(self,jid):
        with self.db() as c: r=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not r: return None
        d=dict(r); d['payload']=json.loads(d['payload']); d['logs']=json.loads(d['logs']); return d

    def jobs(self):
        with self.db() as c: ids=[r[0] for r in c.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT 80')]
        return [self.job(jid) for jid in ids]

    def update_job(self,jid,**values):
        with self.db() as c:
            if 'log' in values:
                logs=json.loads(c.execute('SELECT logs FROM jobs WHERE id=?',(jid,)).fetchone()[0])
                logs.append({'time':now(),'text':str(values.pop('log'))[:1500]}); values['logs']=json.dumps(logs[-150:],ensure_ascii=False)
            allowed={'status','progress','total','added','duplicates','errors','message','logs'}
            if set(values)-allowed: raise ValueError('Unsupported job field')
            values['updated_at']=now(); c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',list(values.values())+[jid])
