"""Serial worker with durable status, cancellation and partial failure reporting."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from .store import now
from .collector import Collector
from .network import download_image
from .sources import Sources

class Cancelled(Exception): pass
class Context:
    def __init__(self,owner,jid): self.owner=owner; self.id=jid; self.cancel=threading.Event(); self.finish=threading.Event()
    def update(self,**values): self.owner.store.update_job(self.id,**values)
    def check(self):
        if self.cancel.is_set(): raise Cancelled()

class Jobs:
    def __init__(self,store,vision,search):
        self.store=store; self.vision=vision; self.search=search; self.collector=Collector(store); self.sources=Sources(store)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pinarchive'); self.contexts={}; self.lock=threading.Lock()
        self.active_sources=set(); self.scheduler_stop=threading.Event(); self.scheduler_thread=None
        with store.db() as c: c.execute("UPDATE jobs SET status='interrupted',message='이전 실행이 종료되었습니다. 다시 실행하면 중복 이미지는 병합됩니다.',updated_at=? WHERE status IN ('queued','running')",(now(),))
    def start(self):
        if self.scheduler_thread is None:
            self.scheduler_thread=threading.Thread(target=self._schedule,name='pinarchive-scheduler',daemon=True)
            self.scheduler_thread.start()
    def _schedule(self):
        while not self.scheduler_stop.is_set():
            try:
                for source_id in self.sources.due():
                    if self.scheduler_stop.is_set(): break
                    try: self.submit('sync',{'source_id':source_id})
                    except ValueError: pass
            except Exception:
                # Leave the source due; a later poll can try again.
                pass
            self.scheduler_stop.wait(15)
    def submit(self,kind,payload):
        if kind not in ('collect','sync','login','url_import','analyze','index'): raise ValueError('지원하지 않는 작업입니다.')
        with self.lock:
            if len(self.contexts)>=20: raise ValueError('대기 작업이 많습니다. 기존 작업을 마친 뒤 등록하세요.')
            source_id=payload.get('source_id') if kind=='sync' else None
            if source_id and self.sources.get(source_id) is None: raise ValueError('등록된 수집 보드를 찾을 수 없습니다.')
            if source_id and source_id in self.active_sources: raise ValueError('이 보드는 이미 수집 중이거나 대기 중입니다.')
            jid=uuid.uuid4().hex; stamp=now()
            with self.store.db() as c:
                c.execute('INSERT INTO jobs(id,kind,status,payload,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (jid,kind,'queued',json.dumps(payload,ensure_ascii=False),
                     self.sources.get(source_id)['download_limit'] if source_id else payload.get('limit',len(payload.get('ids',payload.get('items',[])))),stamp,stamp))
            if source_id:
                self.active_sources.add(source_id); self.sources.mark_queued(source_id)
            ctx=Context(self,jid); self.contexts[jid]=ctx
        self.pool.submit(self.run,ctx,kind,payload); return self.store.job(jid)
    def safe_error(self,e):
        msg=str(e)[:1400] or type(e).__name__; key=self.vision.key()
        return msg.replace(key,'[REDACTED]') if key else msg
    def run(self,ctx,kind,payload):
        try:
            ctx.check(); ctx.update(status='running',message='작업을 시작했습니다.')
            if kind=='sync': self.collector.sync(ctx,payload,self.sources)
            elif kind in ('collect','login'): getattr(self.collector,kind)(ctx,payload)
            else:
                if kind=='index':
                    ctx.update(message='CLIP 모델을 로드합니다. 최초 실행은 모델 다운로드가 필요합니다.'); self.search.load(download=True)
                good=dupes=bad=0
                for i,item in enumerate(payload.get('items',payload.get('ids',[]))):
                    ctx.check()
                    try:
                        if kind=='url_import':
                            raw=download_image(item['image_url']); _,fresh=self.store.add_image(raw,item); good+=int(fresh); dupes+=int(not fresh)
                        elif kind=='analyze': self.vision.analyze(item); good+=1
                        else: self.search.index_one(item); good+=1
                    except Exception as e: bad+=1; ctx.update(log=f'항목 {i+1}: '+self.safe_error(e))
                    ctx.update(progress=i+1,added=good,duplicates=dupes,errors=bad,message=f'{i+1}개 처리 · 성공/신규 {good} · 중복 {dupes} · 실패 {bad}')
            ctx.check(); job=self.store.job(ctx.id)
            status='completed_with_errors' if job['errors'] else 'completed'
            if kind!='sync' and job['errors'] and not job['added'] and not job['duplicates']: status='failed'
            ctx.update(status=status)
        except Cancelled: ctx.update(status='cancelled',message='중단했습니다. 이미 저장된 결과는 유지됩니다.')
        except Exception as e: ctx.update(status='failed',message=self.safe_error(e),log=self.safe_error(e))
        finally:
            try:
                if kind=='sync':
                    job=self.store.job(ctx.id)
                    self.sources.mark_run(payload['source_id'],job['status'],job['message'])
            finally:
                with self.lock:
                    self.contexts.pop(ctx.id,None)
                    if kind=='sync': self.active_sources.discard(payload['source_id'])
    def cancel(self,jid):
        with self.lock: ctx=self.contexts.get(jid)
        if ctx: ctx.cancel.set()
        return bool(ctx)
    def finish_login(self,jid):
        with self.lock: ctx=self.contexts.get(jid)
        if ctx and self.store.job(jid)['kind']=='login': ctx.finish.set(); return True
        return False
    def close(self):
        self.scheduler_stop.set()
        if self.scheduler_thread: self.scheduler_thread.join(timeout=2)
        with self.lock:
            for ctx in self.contexts.values(): ctx.cancel.set()
        self.pool.shutdown(wait=True,cancel_futures=False)
