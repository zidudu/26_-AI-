"""SQLite repository. Original source, analysis and execution snapshots are separate."""
from contextlib import closing, contextmanager
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sqlite3
import uuid
from .common import Problem, dumps, digest, stamp, parse_time
from .settings import defaults, normalized, keyword_id

STATES={'queued':'대기','running':'실행 중','stopping':'중지 요청','completed':'완료',
        'partial':'완료(일부 확인 필요)','failed':'오류','cancelled':'사용자 중지',
        'interrupted':'중단 · 프로그램 종료','mail_unknown':'메일 발송 확인 필요','imported':'이관 완료'}
TYPES={'quality_issue':'품질 이슈','issue_experience':'품질 이슈','question_information':'사용 문의',
       'repair_review':'수리·해결 후기','advertisement':'광고·홍보','promotion':'광고·홍보',
       'other':'기타','unclear':'분류 확인 필요'}


class Database:
    def __init__(self, directory):
        self.directory=Path(directory).resolve();self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'monitoring.sqlite3'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            if c.execute('SELECT version FROM schema_info').fetchone()[0]!=1:
                raise Problem('이 DB 버전은 현재 V10에서 열 수 없습니다.')
            if not c.execute('SELECT 1 FROM cafes LIMIT 1').fetchone():self._catalog(c,defaults()['catalog'])

    @contextmanager
    def connect(self, *, write=False):
        c=sqlite3.connect(self.path,timeout=30)
        c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=30000')
        try:
            if write:c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback();raise
        finally:c.close()

    @staticmethod
    def _put(c,key,value):
        c.execute('INSERT INTO settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',(key,dumps(value),stamp()))

    @staticmethod
    def _get(c,key,fallback=None):
        r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else fallback

    @classmethod
    def _bump(cls,c):cls._put(c,'sequence',cls._get(c,'sequence',0)+1)

    def get(self,key,fallback=None):
        with self.connect() as c:return self._get(c,key,fallback)

    def put(self,key,value):
        with self.connect(write=True) as c:self._put(c,key,value);self._bump(c)

    def catalog(self):
        with self.connect() as c:
            cafes=[dict(id=r['id'],naverCafeId=r['naver_id'],code=r['code'],name=r['name'],url=r['url'],active=bool(r['active']),createdAt=r['created_at']) for r in c.execute('SELECT * FROM cafes ORDER BY rowid')]
            keys=[dict(id=r['id'],name=r['name'],active=bool(r['active']),deleted=bool(r['deleted']),createdAt=r['created_at']) for r in c.execute('SELECT * FROM keywords ORDER BY rowid')]
            return dict(cafes=cafes,keywords=keys)

    @staticmethod
    def _catalog(c,catalog,*,update_existing=True):
        for row in catalog['cafes']:
            old=c.execute('SELECT * FROM cafes WHERE id=?',(row['id'],)).fetchone()
            if old and (old['url']!=row['url'] or old['code']!=row['code']):
                raise Problem('기존 카페의 코드·주소는 변경할 수 없습니다.')
            # A browser cannot silently replace a verified Naver numeric identity.
            cid=old['naver_id'] if old else None
            if old and not update_existing:continue
            c.execute('INSERT INTO cafes VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=excluded.active',
                (row['id'],cid,row['code'],row['name'],row['url'],int(row['active']) if update_existing else 0,row.get('createdAt') or stamp()))
        for row in catalog['keywords']:
            if not update_existing and c.execute('SELECT 1 FROM keywords WHERE id=?',(row['id'],)).fetchone():continue
            c.execute('INSERT INTO keywords VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=excluded.active,deleted=excluded.deleted',
                (row['id'],row['name'],normalized(row['name']),int(row['active']) if update_existing else 0,int(row['deleted']) if update_existing else 1,row.get('createdAt') or stamp()))

    def save_settings(self,cfg,expected_revision):
        with self.connect(write=True) as c:
            rev=self._get(c,'settings_revision',0)
            if type(expected_revision) is not int or expected_revision!=rev:
                raise Problem('다른 창에서 설정이 바뀌었습니다. 새로고침 후 저장하세요.',409,'SETTINGS_CONFLICT')
            self._catalog(c,cfg['catalog']);self._put(c,'ui_settings',cfg)
            self._put(c,'settings_revision',rev+1);self._bump(c)
            return rev+1

    def resolve_cafe(self,uid,naver_id):
        if not str(naver_id).isdigit():raise Problem('네이버 카페 ID를 확인하지 못했습니다.')
        with self.connect(write=True) as c:
            old=c.execute('SELECT naver_id FROM cafes WHERE id=?',(uid,)).fetchone()
            if not old:raise Problem('등록된 카페를 찾지 못했습니다.')
            if old[0] and old[0]!=str(naver_id):raise Problem('기존 연결과 네이버 카페 ID가 다릅니다.')
            try:c.execute('UPDATE cafes SET naver_id=? WHERE id=?',(str(naver_id),uid))
            except sqlite3.IntegrityError:raise Problem('같은 네이버 카페가 다른 카페로 등록되어 있습니다.') from None

    def cursors(self):
        with self.connect() as c:return {r['cafe_id']:r['completed_until'] for r in c.execute('SELECT * FROM cursors')}

    def create_run(self,rid,cfg,bounds,*,request_id,trigger='manual',record=None,state='queued'):
        at=(record or {}).get('startedAt') or stamp()
        extra={'type':'예약 실행' if trigger=='schedule' else '정규 실행' if cfg['stepCollect'] else '저장 결과 재사용',
               'stepsDone':[],'plannedSteps':[k for k,on in [('collect',cfg['stepCollect']),('analyze',cfg['stepAnalyze']),('report',cfg['stepPpt']),('mail',cfg['sendMail'])] if on],
               'source':'database','hasRaw':False,'hasAnalysis':False,'notes':'',
               'summaryCafes':deepcopy(cfg['summaryCafes']),'mailStatus':'not_started' if cfg['sendMail'] else 'disabled'}
        extra.update(record or {})
        with self.connect(write=True) as c:
            existing=c.execute('SELECT id FROM runs WHERE request_id=?',(request_id,)).fetchone()
            if existing:return existing[0],False
            self._catalog(c,cfg['catalog'],update_existing=False)
            try:c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,0)',
                (rid,request_id,trigger,state,'prepare',at,None,at,dumps(cfg),cfg.get('sourceRun') or None,dumps(extra)))
            except sqlite3.IntegrityError as exc:
                if c.execute("SELECT 1 FROM runs WHERE state IN ('queued','running','stopping')").fetchone():
                    raise Problem('실행 중인 작업이 있습니다.',409,'RUN_BUSY') from None
                raise Problem('원본 실행 또는 요청 식별자를 확인하세요.',409) from exc
            for uid,(lo,hi) in bounds.items():c.execute('INSERT INTO run_cafes VALUES(?,?,?,?,?,?,?)',(rid,uid,lo,hi,'queued',None,None))
            self._bump(c)
        return rid,True

    def update_run(self,rid,*,state=None,stage=None,done=None,finish=False,**values):
        with self.connect(write=True) as c:
            r=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not r:raise Problem('실행을 찾을 수 없습니다.',404)
            extra=json.loads(r['record_json']);extra.update(values)
            if done and done not in extra['stepsDone']:extra['stepsDone'].append(done)
            c.execute('UPDATE runs SET state=?,stage=?,updated_at=?,ended_at=?,record_json=? WHERE id=?',
                (state or r['state'],stage or r['stage'],stamp(),stamp() if finish else r['ended_at'],dumps(extra),rid))
            self._bump(c)

    def active(self):
        with self.connect() as c:
            r=c.execute("SELECT id FROM runs WHERE state IN ('queued','running','stopping')").fetchone()
            return r[0] if r else None

    def run_row(self,rid):
        with self.connect() as c:
            r=c.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()
            if not r:raise Problem('실행을 찾을 수 없습니다.',404)
            return dict(r)

    def stop(self,rid):
        with self.connect(write=True) as c:
            c.execute("UPDATE runs SET stop_requested=1,state='stopping',updated_at=? WHERE id=? AND state IN ('queued','running','stopping')",(stamp(),rid));self._bump(c)

    def check_stop(self,rid):
        if self.run_row(rid)['stop_requested']:raise InterruptedError('사용자가 실행 중지를 요청했습니다.')

    def recover(self):
        with self.connect(write=True) as c:
            c.execute("UPDATE mail_logs SET state='unknown' WHERE state='sending'")
            for r in c.execute("SELECT * FROM runs WHERE state IN ('queued','running','stopping')").fetchall():
                mail=c.execute('SELECT state FROM mail_logs WHERE run_id=?',(r['id'],)).fetchone()
                state='mail_unknown' if mail and mail[0]=='unknown' else 'interrupted'
                extra=json.loads(r['record_json']);extra['notes']='이전 실행이 종료되지 않았습니다. 저장 결과를 확인한 뒤 새 실행으로 재사용하세요.'
                if state=='mail_unknown':extra['mailStatus']='unknown'
                c.execute('UPDATE runs SET state=?,ended_at=?,updated_at=?,record_json=? WHERE id=?',(state,stamp(),stamp(),dumps(extra),r['id']))
            self._bump(c)

    def log(self,rid,message,level='INFO'):
        if not str(message).strip():return
        with self.connect(write=True) as c:c.execute('INSERT INTO logs(run_id,at,level,message) VALUES(?,?,?,?)',(rid,stamp(),level,str(message)[:16000]))

    def logs(self,rid,after=0,limit=2000):
        with self.connect() as c:return [dict(r) for r in c.execute('SELECT * FROM logs WHERE run_id=? AND id>? ORDER BY id LIMIT ?',(rid,after,limit))]

    def cafe_outcome(self,rid,uid,state,count=None,error=None,*,advance=False):
        with self.connect(write=True) as c:
            row=c.execute('SELECT * FROM run_cafes WHERE run_id=? AND cafe_id=?',(rid,uid)).fetchone()
            if not row:raise Problem('실행 카페 관계가 없습니다.')
            c.execute('UPDATE run_cafes SET status=?,count=?,error=? WHERE run_id=? AND cafe_id=?',(state,count,error,rid,uid))
            if advance and state=='completed':
                old=c.execute('SELECT completed_until FROM cursors WHERE cafe_id=?',(uid,)).fetchone()
                if not old or parse_time(old[0])<parse_time(row['end_at']):
                    c.execute('INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(cafe_id) DO UPDATE SET completed_until=excluded.completed_until,run_id=excluded.run_id',(uid,row['end_at'],rid))
            self._bump(c)

    def store_article(self,rid,uid,item,source,*,dedupe=False,observed=None):
        aid=str(item['id']);observed=observed or stamp(parse_time(item.get('collected_at') or source.get('collected_at') or stamp()))
        if not aid.isdigit():raise Problem('게시글 번호가 올바르지 않습니다.')
        raw=source.get('body_raw',source.get('body',''))
        if not isinstance(raw,str):raise Problem('원문 본문 형식이 올바르지 않습니다.')
        title=source.get('title',item.get('title',''));url=source.get('url',item.get('url',''))
        published=source.get('written_at',item.get('written_at'))
        fingerprint=digest({'title':title,'raw':raw,'published':published,'media':source.get('media',source.get('images',[]))})
        matched=list(dict.fromkeys(item.get('matched_keywords',[])))
        with self.connect(write=True) as c:
            p=c.execute('SELECT * FROM posts WHERE cafe_id=? AND article_id=?',(uid,aid)).fetchone()
            if p:
                pid=p['id'];previous=c.execute('SELECT fingerprint FROM post_versions WHERE id=?',(p['latest_version_id'],)).fetchone()
                disposition='unchanged' if previous and previous[0]==fingerprint else 'changed'
                c.execute('UPDATE posts SET last_checked_at=max(last_checked_at,?),first_collected_at=min(first_collected_at,?) WHERE id=?',(observed,observed,pid))
            else:
                pid=c.execute('INSERT INTO posts(cafe_id,article_id,first_collected_at,last_checked_at) VALUES(?,?,?,?)',(uid,aid,observed,observed)).lastrowid
                disposition='new'
            c.execute('INSERT OR IGNORE INTO post_versions(post_id,fingerprint,title,raw,published_at,url,source_json,observed_at) VALUES(?,?,?,?,?,?,?,?)',
                (pid,fingerprint,title,raw,published,url,dumps(source),observed))
            vid=c.execute('SELECT id FROM post_versions WHERE post_id=? AND fingerprint=?',(pid,fingerprint)).fetchone()[0]
            # An older import must never replace a newer collected original.
            if not p or parse_time(observed)>=parse_time(p['last_checked_at']):
                c.execute('UPDATE posts SET latest_version_id=? WHERE id=?',(vid,pid))
            for word in matched:
                kid=keyword_id(word)
                c.execute('INSERT OR IGNORE INTO keywords VALUES(?,?,?,?,?,?)',(kid,word,normalized(word),0,1,observed))
                c.execute('INSERT INTO post_keywords VALUES(?,?,?,?) ON CONFLICT(post_id,keyword_id) DO UPDATE SET last_seen_at=max(last_seen_at,excluded.last_seen_at)',(pid,kid,observed,observed))
            include=not(dedupe and disposition=='unchanged')
            if not include:disposition='skipped'
            c.execute('INSERT INTO run_posts VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,post_id) DO UPDATE SET version_id=excluded.version_id,included=excluded.included,disposition=excluded.disposition,matched_json=excluded.matched_json,engine_json=excluded.engine_json',
                (rid,pid,vid,None,int(include),disposition,dumps(matched),dumps(item)))
            self._bump(c)
            return dict(post_id=pid,version_id=vid,included=include,disposition=disposition)

    def save_analysis(self,rid,uid,item,phase='analysis'):
        with self.connect(write=True) as c:
            r=c.execute('SELECT rp.* FROM run_posts rp JOIN posts p ON p.id=rp.post_id WHERE rp.run_id=? AND p.cafe_id=? AND p.article_id=?',(rid,uid,str(item['id']))).fetchone()
            if not r:raise Problem('원문이 저장되지 않은 분석입니다.')
            c.execute('INSERT INTO analyses(version_id,run_id,result_json,created_at,phase) VALUES(?,?,?,?,?) ON CONFLICT(run_id,version_id,phase) DO UPDATE SET result_json=excluded.result_json',
                (r['version_id'],rid,dumps(item),stamp(),phase))
            aid=c.execute('SELECT id FROM analyses WHERE run_id=? AND version_id=? AND phase=?',(rid,r['version_id'],phase)).fetchone()[0]
            c.execute('UPDATE run_posts SET analysis_id=?,engine_json=? WHERE run_id=? AND post_id=?',(aid,dumps(item),rid,r['post_id']));self._bump(c)

    def reuse(self,rid,source_id,*,require_analysis=False):
        with self.connect(write=True) as c:
            rows=c.execute('SELECT * FROM run_posts WHERE run_id=? AND included=1',(source_id,)).fetchall()
            if not rows:raise Problem('원본 실행에 재사용할 게시글이 없습니다.')
            if require_analysis and any(not r['analysis_id'] for r in rows):raise Problem('원본 실행의 분석이 일부 없습니다. AI 분석을 먼저 실행하세요.')
            for r in rows:c.execute('INSERT INTO run_posts VALUES(?,?,?,?,?,?,?,?)',(rid,r['post_id'],r['version_id'],r['analysis_id'],1,'reused',r['matched_json'],r['engine_json']))
            self._bump(c)

    def engine_items(self,rid):
        with self.connect() as c:return [(r['cafe_id'],json.loads(r['engine_json'])) for r in c.execute('SELECT rp.*,p.cafe_id FROM run_posts rp JOIN posts p ON p.id=rp.post_id WHERE rp.run_id=? AND included=1 ORDER BY rp.rowid',(rid,))]

    @staticmethod
    def _present(row,keys,*,analysis=None,run_id=None,review=None):
        a=analysis or {};candidate=a.get('analysis_candidate') or {}
        bindings=a.get('analysis_bindings') or []
        text=a.get('display_text') or a.get('ai_summary') or ''
        if isinstance(bindings,dict):bindings=list(bindings.values())
        evidence=[]
        for b in bindings if isinstance(bindings,list) else []:
            if isinstance(b,dict):
                value=b.get('text') or b.get('quote') or b.get('source_text')
                if value:evidence.append(str(value))
                for quote in b.get('quotes',[]):
                    if isinstance(quote,dict) and isinstance(quote.get('text'),str):
                        evidence.append(str(quote.get('id',''))+' · '+quote['text'])
        evidence=list(dict.fromkeys(evidence))
        if not evidence and candidate:evidence.append(dumps({'bindings':a.get('analysis_bindings',{}),'candidate':candidate}))
        status='검토완료' if review=='approved' else '미검토'
        return dict(cafe=row['code'],cafeId=row['cafe_id'],id=row['article_id'],postId=row['post_id'],versionId=row['version_id'],
            analysisId=row['analysis_id'] if 'analysis_id' in row.keys() else None,title=row['title'],url=row['url'],
            raw=row['raw'],key=', '.join(keys),keys=keys,type=TYPES.get(a.get('document_type'),'분석 대기' if not a else a.get('document_type','분류 확인 필요')),
            status=status,issue=text,analysis=text,evidence='\n'.join(evidence),doc=TYPES.get(a.get('document_type'),'분석 대기'),
            vehicle=a.get('vehicle','-'),date=row['first_collected_at'][:10],publishedAt=row['published_at'],
            firstCollectedAt=row['first_collected_at'],lastCheckedAt=row['last_checked_at'],runId=run_id,
            analysisIssues=a.get('analysis_issues',[]))

    def articles(self,rid):
        with self.connect() as c:
            rows=c.execute('''SELECT p.id post_id,p.cafe_id,p.article_id,p.first_collected_at,p.last_checked_at,
                v.id version_id,v.title,v.raw,v.url,v.published_at,rp.analysis_id,rp.matched_json,a.result_json,cf.code,
                (SELECT state FROM reviews WHERE analysis_id=rp.analysis_id ORDER BY id DESC LIMIT 1) review
                FROM run_posts rp JOIN posts p ON p.id=rp.post_id JOIN post_versions v ON v.id=rp.version_id
                JOIN cafes cf ON cf.id=p.cafe_id LEFT JOIN analyses a ON a.id=rp.analysis_id
                WHERE rp.run_id=? AND rp.included=1 ORDER BY rp.rowid''',(rid,)).fetchall()
            return [self._present(r,json.loads(r['matched_json']),analysis=json.loads(r['result_json']) if r['result_json'] else None,run_id=rid,review=r['review']) for r in rows]

    def stats(self,after=0,limit=500):
        with self.connect() as c:
            rows=c.execute('''SELECT p.id post_id,p.cafe_id,p.article_id,p.first_collected_at,p.last_checked_at,
                v.id version_id,v.title,v.raw,v.url,v.published_at,cf.code,a.id analysis_id,a.result_json,
                (SELECT rp.run_id FROM run_posts rp JOIN runs rn ON rn.id=rp.run_id WHERE rp.version_id=v.id ORDER BY rn.started_at DESC LIMIT 1) run_id,
                (SELECT state FROM reviews WHERE analysis_id=a.id ORDER BY id DESC LIMIT 1) review
                FROM posts p JOIN post_versions v ON v.id=p.latest_version_id JOIN cafes cf ON cf.id=p.cafe_id
                LEFT JOIN analyses a ON a.id=(SELECT an.id FROM analyses an JOIN runs ar ON ar.id=an.run_id WHERE an.version_id=v.id ORDER BY ar.started_at DESC,an.id DESC LIMIT 1)
                WHERE p.id>? ORDER BY p.id LIMIT ?''',(after,limit)).fetchall()
            result=[]
            for r in rows:
                keys=[k[0] for k in c.execute('SELECT k.name FROM post_keywords pk JOIN keywords k ON k.id=pk.keyword_id WHERE pk.post_id=? ORDER BY k.rowid',(r['post_id'],))]
                result.append(self._present(r,keys,analysis=json.loads(r['result_json']) if r['result_json'] else None,run_id=r['run_id'],review=r['review']))
            return result

    def review(self,analysis_id,state='approved',note=''):
        if state not in ('approved','pending') or not isinstance(note,str) or len(note)>5000:raise Problem('검토 형식을 확인하세요.')
        with self.connect(write=True) as c:
            if not c.execute('SELECT 1 FROM analyses WHERE id=?',(analysis_id,)).fetchone():raise Problem('검토할 저장 분석이 없습니다.',404)
            c.execute('INSERT INTO reviews(analysis_id,state,note,reviewed_at) VALUES(?,?,?,?)',(analysis_id,state,note,stamp()));self._bump(c)

    def add_artifact(self,rid,kind,path):
        path=Path(path).resolve()
        if not path.is_file():raise Problem('결과 파일이 없습니다.')
        with path.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
        with self.connect(write=True) as c:
            old=c.execute('SELECT id FROM artifacts WHERE run_id=? AND kind=? AND path=?',(rid,kind,str(path))).fetchone()
            aid=old[0] if old else uuid.uuid4().hex
            c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id,kind,path) DO UPDATE SET sha256=excluded.sha256,size=excluded.size',(aid,rid,kind,str(path),h,path.stat().st_size,stamp()));self._bump(c)
        return aid

    def artifact(self,aid,*,verify=True):
        with self.connect() as c:r=c.execute('SELECT * FROM artifacts WHERE id=?',(aid,)).fetchone()
        if not r:raise Problem('등록된 결과 파일이 없습니다.',404)
        r=dict(r);p=Path(r['path'])
        if not p.is_file():raise Problem('파일이 이동되었거나 삭제되었습니다.',404,'FILE_MISSING')
        if verify:
            with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
            if h!=r['sha256']:raise Problem('저장 이후 결과 파일이 변경되었습니다.',409,'FILE_CHANGED')
        return r

    def mail_start(self,rid,recipients,attachment_id):
        with self.connect(write=True) as c:
            stopped=c.execute('SELECT stop_requested FROM runs WHERE id=?',(rid,)).fetchone()
            if stopped and stopped[0]:raise InterruptedError('메일 시작 전에 중지 요청이 도착했습니다.')
            if c.execute('SELECT 1 FROM mail_logs WHERE run_id=?',(rid,)).fetchone():raise Problem('메일 전송 기록이 이미 있습니다. 자동으로 다시 보내지 않습니다.',409)
            c.execute('INSERT INTO mail_logs(run_id,state,recipients_json,attachment_id,started_at) VALUES(?,?,?,?,?)',(rid,'sending',dumps(recipients),attachment_id,stamp()));self._bump(c)

    def mail_finish(self,rid,state,result):
        with self.connect(write=True) as c:
            c.execute('UPDATE mail_logs SET state=?,finished_at=?,result_json=? WHERE run_id=?',(state,stamp(),dumps(result),rid));self._bump(c)
        self.update_run(rid,mailStatus=state)

    def run(self,rid,*,details=True):
        r=self.run_row(rid);extra=json.loads(r['record_json']);cfg=json.loads(r['settings_json'])
        with self.connect() as c:
            rc=[dict(x) for x in c.execute('SELECT * FROM run_cafes WHERE run_id=?',(rid,))]
            artifacts=[dict(x) for x in c.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id',(rid,))]
            counts={x[0]:x[1] for x in c.execute('SELECT disposition,count(*) FROM run_posts WHERE run_id=? GROUP BY disposition',(rid,))}
            included=c.execute('SELECT count(*) FROM run_posts WHERE run_id=? AND included=1',(rid,)).fetchone()[0]
        out={**extra,'id':rid,'state':r['state'],'status':STATES[r['state']],'stageKey':r['stage'],
             'stage':{'prepare':'준비','collect':'수집','analyze':'AI 분석','report':'PPT 생성','mail':'메일 발송','complete':'완료'}.get(r['stage'],r['stage']),
             'startedAt':r['started_at'],'endedAt':r['ended_at'],'updatedAt':r['updated_at'],'settings':cfg,
             'sourceRun':r['source_run_id'],'source':'database','provider':cfg['provider'],'keywords':str(len(cfg['keywords']))+'개',
             'parallel':cfg['parallel']+'개','posts':str(included)+'건','slides':extra.get('slides','—'),
             'cafes':str(sum(x['status']=='completed' for x in rc))+'개 완료 / '+str(len(rc))+'개' if rc else '저장 결과 재사용',
             'period':cfg['range'] if cfg['stepCollect'] else '저장 결과 재사용',
             'periodMode':cfg['range'],'periodStart':min((x['start_at'] for x in rc),default=extra.get('periodStart')),
             'periodEnd':max((x['end_at'] for x in rc),default=extra.get('periodEnd')),
             'completedCafeIds':[x['cafe_id'] for x in rc if x['status']=='completed'],'cafeResults':rc,
             'newArticles':counts.get('new',0),'articlesUpdated':counts.get('changed',0),'duplicatesSkipped':counts.get('skipped',0),
             'articlesRecollected':counts.get('unchanged',0),'artifacts':artifacts}
        if details:
            out['articles']=self.articles(rid)
            out['rows']=[[a['cafe'],a['id'],a['key'],a['type'],a['status'],'DB'] for a in out['articles']]
            out['logText']='\n'.join('['+x['at'][11:19]+'] '+x['message'] for x in self.logs(rid))
        return out

    def runs(self):
        with self.connect() as c:ids=[r[0] for r in c.execute('SELECT id FROM runs ORDER BY started_at DESC,id DESC')]
        return [self.run(i) for i in ids]

    def counts(self):
        with self.connect() as c:return {t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('posts','post_versions','runs','analyses','reviews','artifacts')}

    def backup(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        # sqlite3.Connection.__exit__ commits/rolls back; it does not close.
        # Close the destination even if backup raises (Windows keeps it locked).
        with self.connect() as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        return path
