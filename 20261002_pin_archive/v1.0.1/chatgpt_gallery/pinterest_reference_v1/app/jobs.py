"""Serial worker with durable status, cancellation and partial failure reporting."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from .store import now
from .collector import Collector
from .network import download_image

class Cancelled(Exception): pass
class Context:
    def __init__(self,owner,jid): self.owner=owner; self.id=jid; self.cancel=threading.Event(); self.finish=threading.Event()
    def update(self,**values): self.owner.store.update_job(self.id,**values)
    def check(self):
        if self.cancel.is_set(): raise Cancelled()

class Jobs:
    def __init__(self,store,vision,search):
        self.store=store; self.vision=vision; self.search=search; self.collector=Collector(store)
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pinarchive'); self.contexts={}; self.lock=threading.Lock()
        with store.db() as c: c.execute("UPDATE jobs SET status='interrupted',message='이전 실행이 종료되었습니다. 다시 실행하면 중복 이미지는 병합됩니다.',updated_at=? WHERE status IN ('queued','running')",(now(),))
    def submit(self,kind,payload):
        if kind not in ('collect','login','url_import','analyze','index'): raise ValueError('지원하지 않는 작업입니다.')
        with self.lock:
            if len(self.contexts)>=20: raise ValueError('대기 작업이 많습니다. 기존 작업을 마친 뒤 등록하세요.')
            jid=uuid.uuid4().hex; stamp=now()
            with self.store.db() as c:
                c.execute('INSERT INTO jobs(id,kind,status,payload,total,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (jid,kind,'queued',json.dumps(payload,ensure_ascii=False),payload.get('limit',len(payload.get('ids',payload.get('items',[])))),stamp,stamp))
            ctx=Context(self,jid); self.contexts[jid]=ctx
        self.pool.submit(self.run,ctx,kind,payload); return self.store.job(jid)
    def safe_error(self,e):
        msg=str(e)[:1400] or type(e).__name__; key=self.vision.key()
        return msg.replace(key,'[REDACTED]') if key else msg
    def run(self,ctx,kind,payload):
        try:
            ctx.check(); ctx.update(status='running',message='작업을 시작했습니다.')
            if kind in ('collect','login'): getattr(self.collector,kind)(ctx,payload)
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
            if job['errors'] and not job['added'] and not job['duplicates']: status='failed'
            ctx.update(status=status)
        except Cancelled: ctx.update(status='cancelled',message='중단했습니다. 이미 저장된 결과는 유지됩니다.')
        except Exception as e: ctx.update(status='failed',message=self.safe_error(e),log=self.safe_error(e))
        finally:
            with self.lock: self.contexts.pop(ctx.id,None)
    def cancel(self,jid):
        with self.lock: ctx=self.contexts.get(jid)
        if ctx: ctx.cancel.set()
        return bool(ctx)
    def finish_login(self,jid):
        with self.lock: ctx=self.contexts.get(jid)
        if ctx and self.store.job(jid)['kind']=='login': ctx.finish.set(); return True
        return False
    def close(self):
        with self.lock:
            for ctx in self.contexts.values(): ctx.cancel.set()
        self.pool.shutdown(wait=True,cancel_futures=False)
