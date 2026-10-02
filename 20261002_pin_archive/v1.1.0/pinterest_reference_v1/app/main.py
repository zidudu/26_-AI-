"""Local-only FastAPI application. Not a public multi-user hosting service."""
from __future__ import annotations
import io
import json
import os
import secrets
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI,Request,HTTPException,Query,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .store import Store,VERSION,MAX_IMAGE_BYTES,tags
from .ai import Vision
from .search import Search,SemanticUnavailable,INDEX_MODEL
from .jobs import Jobs
from .collector import target_url

ROOT=Path(__file__).resolve().parents[1]
class Strict(BaseModel): model_config=ConfigDict(extra='forbid')
class EditPin(Strict):
    title:str|None=Field(None,max_length=300)
    description:str|None=Field(None,max_length=6000)
    category:str|None=Field(None,max_length=80)
    tags:list[str]|None=Field(None,max_length=80)
    author:str|None=Field(None,max_length=160)
    source_url:str|None=Field(None,max_length=4096)
    license_note:str|None=Field(None,max_length=2000)
    favorite:bool|None=None
class Ids(Strict):
    ids:list[str]=Field(default_factory=list,max_length=500)
    consent:bool=False
class CollectionBody(Strict): name:str=Field(min_length=1,max_length=80)
class Membership(Strict):
    ids:list[str]=Field(min_length=1,max_length=500)
    add:bool=True
class CollectBody(Strict):
    target:str=Field(min_length=1,max_length=2000)
    limit:int=Field(50,ge=1,le=500)
    collection_id:str=''
    tags:list[str]=Field(default_factory=list,max_length=40)
    permission_confirmed:bool=False
class SourceBody(Strict):
    name:str=Field(min_length=1,max_length=80)
    target:str=Field(min_length=1,max_length=2000)
    collection_id:str=Field(min_length=1)
    interval_minutes:int=Field(60,ge=15,le=10080)
    scan_limit:int=Field(500,ge=1,le=1000)
    download_limit:int=Field(50,ge=1,le=200)
    enabled:bool=True
    permission_confirmed:bool=False
class SourceEdit(Strict):
    name:str|None=Field(None,min_length=1,max_length=80)
    collection_id:str|None=None
    interval_minutes:int|None=Field(None,ge=15,le=10080)
    scan_limit:int|None=Field(None,ge=1,le=1000)
    download_limit:int|None=Field(None,ge=1,le=200)
    enabled:bool|None=None
class UrlItem(Strict):
    image_url:str=Field('',max_length=4096)
    title:str=Field('가져온 이미지',max_length=300)
    description:str=Field('',max_length=6000)
    source_url:str=Field('',max_length=4096)
    pin_url:str=Field('',max_length=4096)
    source:Literal['local','url','pinterest','import']='import'
    category:str=Field('미분류',max_length=80)
    author:str=Field('',max_length=160)
    tags:list[str]=Field(default_factory=list,max_length=80)
    crawl_keyword:str=Field('',max_length=2000)
    collection_id:str=''
    license_note:str=Field('권리 미확인 · 원본 출처와 이용 조건을 확인하세요.',max_length=2000)
class UrlImport(Strict):
    items:list[UrlItem]=Field(min_length=1,max_length=100)
    permission_confirmed:bool=False
class SettingsBody(Strict):
    provider:Literal['none','openai','ollama']='none'
    openai_model:str=Field('gpt-4.1-mini',max_length=120)
    ollama_model:str=Field('',max_length=120)
    browser_channel:Literal['chromium','chrome','msedge']='chromium'
    api_key:str|None=Field(None,max_length=1000)
class RankBody(Strict):
    query:str=Field(min_length=1,max_length=500)
    ids:list[str]=Field(min_length=1,max_length=12)
    consent:bool=False

def create_app(data_dir:Path|None=None,*,seed=True,instance_id:str|None=None):
    store=Store(data_dir or Path(os.getenv('PINARCHIVE_DATA_DIR',ROOT/'data')))
    if seed: store.seed(ROOT/'seed')
    vision=Vision(store); search=Search(store); jobs=Jobs(store,vision,search); csrf=secrets.token_urlsafe(32)
    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        yield
        jobs.close()
    app=FastAPI(title='Pin Archive',version=VERSION,docs_url=None,redoc_url=None,lifespan=lifespan)
    app.state.store=store; app.state.vision=vision; app.state.search=search; app.state.jobs=jobs; app.state.csrf=csrf
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1'])
    @app.middleware('http')
    async def local_security(request:Request,call_next):
        if request.url.path.startswith(('/api/','/media/')):
            if request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'외부 사이트에서 로컬 자료에 접근할 수 없습니다.'},status_code=403)
            origin=request.headers.get('origin')
            if origin:
                u=urlsplit(origin); here=urlsplit(str(request.base_url))
                if (u.scheme,u.netloc)!=(here.scheme,here.netloc): return JSONResponse({'detail':'같은 출처의 요청만 허용합니다.'},status_code=403)
            if request.method not in ('GET','HEAD','OPTIONS'):
                if not secrets.compare_digest(request.headers.get('x-pinarchive-csrf',''),csrf):
                    return JSONResponse({'detail':'보안 토큰이 만료되었습니다. 페이지를 새로고침하세요.'},status_code=403)
                try:
                    if int(request.headers.get('content-length',0))>100*1024*1024:
                        return JSONResponse({'detail':'한 번에 100 MB 이하로 업로드하세요.'},status_code=413)
                except ValueError: return JSONResponse({'detail':'잘못된 요청 크기입니다.'},status_code=400)
        r=await call_next(request)
        r.headers['X-Content-Type-Options']='nosniff'; r.headers['Referrer-Policy']='no-referrer'
        r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'): r.headers['Cache-Control']='no-store'
        return r
    @app.exception_handler(ValueError)
    async def invalid(request,e): return JSONResponse({'detail':str(e)},status_code=400)
    @app.exception_handler(SemanticUnavailable)
    async def unavailable(request,e): return JSONResponse({'detail':str(e)},status_code=409)
    @app.exception_handler(KeyError)
    async def missing(request,e): return JSONResponse({'detail':'자료를 찾을 수 없습니다.'},status_code=404)
    @app.get('/api/health')
    def health(): return {'app':'pinarchive','version':VERSION,'ok':True,'instance_id':instance_id}
    @app.get('/api/bootstrap')
    def bootstrap(): return {'version':VERSION,'csrf':csrf,'stats':store.stats(),'settings':store.settings(),'ai':vision.status(),'semantic':search.status(),'data_dir':str(store.root),'app_dir':str(ROOT),'platform':os.name}
    @app.get('/api/stats')
    def stats(): return store.stats()
    @app.get('/api/pins')
    def list_pins(q:str=Query('',max_length=500),mode:Literal['keyword','semantic','hybrid']='keyword',limit:int=Query(60,ge=1,le=200),offset:int=Query(0,ge=0),category:str='',source:str='',favorite:bool=False,collection:str='',untagged:bool=False):
        return search.search(q,mode,limit,offset,category=category,source=source,favorite=favorite,collection=collection,untagged=untagged)
    @app.get('/api/pins/{pid}')
    def get_pin(pid:str):
        p=store.get(pid)
        if not p: raise KeyError(pid)
        return p
    @app.patch('/api/pins/{pid}')
    def edit_pin(pid:str,body:EditPin): return store.update(pid,body.model_dump(exclude_none=True))
    @app.delete('/api/pins/{pid}')
    def delete_pin(pid:str): store.delete(pid); return {'deleted':pid}
    @app.get('/api/pins/{pid}/similar')
    def similar(pid:str): return search.similar(pid)
    @app.get('/media/{pid}/{kind}')
    def media(pid:str,kind:Literal['image','thumbnail'],download:bool=False):
        p=store.get(pid)
        if not p: raise KeyError(pid)
        path=store.thumbs/(pid+'.webp') if kind=='thumbnail' else store.images/p['filename']
        if not path.exists(): raise HTTPException(404,'이미지 파일이 없습니다. data 폴더를 확인하세요.')
        return FileResponse(path,filename=p['filename'] if download else None,headers={'Cache-Control':'private,max-age=3600'})
    @app.post('/api/import')
    async def import_files(files:list[UploadFile]=File(...),tags_text:str=Form('',alias='tags'),category:str=Form('미분류'),collection_id:str=Form(''),metadata:str=Form('')):
        if len(files)>50: raise HTTPException(400,'한 번에 50개 이하로 가져오세요.')
        extra=[]
        if metadata:
            try:
                raw_meta=json.loads(metadata)
                if not isinstance(raw_meta,list) or len(raw_meta)!=len(files): raise ValueError()
                extra=[UrlItem.model_validate(m).model_dump() for m in raw_meta]
            except (ValueError,TypeError): raise HTTPException(400,'metadata는 파일 수와 같은 길이의 유효한 객체 배열이어야 합니다.')
        result=[]; errors=[]; added=dupes=total=0
        for i,f in enumerate(files):
            try:
                raw=await f.read(MAX_IMAGE_BYTES+1); total+=len(raw)
                if total>100*1024*1024: raise ValueError('총 업로드 크기가 100 MB를 초과했습니다.')
                name=Path((f.filename or '이미지').replace('\\','/')).stem
                m={'title':name,'source':'local','tags':tags(tags_text),'category':category[:80],'collection_id':collection_id}
                if extra:
                    m.update(extra[i]); m['source']='import'; m['tags']=tags(m['tags']+tags(tags_text))
                    if collection_id: m['collection_id']=collection_id
                p,fresh=store.add_image(raw,m); result.append(p); added+=int(fresh); dupes+=int(not fresh)
            except ValueError as e: errors.append({'file':f.filename,'error':str(e)})
            finally: await f.close()
        return {'items':result,'added':added,'duplicates':dupes,'errors':errors}
    @app.post('/api/import/urls')
    def url_import(body:UrlImport):
        if not body.permission_confirmed: raise ValueError('이미지 접근·이용 권한을 확인하세요.')
        if any(not i.image_url.strip() for i in body.items): raise ValueError('image_url이 비어 있습니다.')
        return jobs.submit('url_import',body.model_dump())
    @app.get('/api/collections')
    def list_collections(): return {'items':store.collections()}
    @app.post('/api/collections')
    def add_collection(body:CollectionBody): return store.create_collection(body.name)
    @app.post('/api/collections/{cid}/pins')
    def membership(cid:str,body:Membership): store.membership(cid,body.ids,body.add); return {'ok':True}
    @app.delete('/api/collections/{cid}')
    def remove_collection(cid:str):
        with store.db() as c:
            if c.execute('SELECT 1 FROM tracked_sources WHERE collection_id=?',(cid,)).fetchone():
                raise HTTPException(409,'반복 수집에 연결된 보드입니다. 수집 설정을 변경하거나 등록 해제한 뒤 삭제하세요.')
            if not c.execute('DELETE FROM collections WHERE id=?',(cid,)).rowcount: raise KeyError(cid)
        return {'ok':True,'note':'보드만 삭제했습니다. 이미지는 유지됩니다.'}
    @app.get('/api/jobs')
    def list_jobs(): return {'items':store.jobs()}
    @app.get('/api/sources')
    def list_sources(): return {'items':jobs.sources.list()}
    @app.post('/api/sources')
    def create_source(body:SourceBody):
        if not body.permission_confirmed: raise ValueError('Pinterest 자동 접근과 이미지 이용 권한을 확인하세요.')
        return jobs.sources.create(name=body.name,target=body.target,collection_id=body.collection_id,
            interval_minutes=body.interval_minutes,scan_limit=body.scan_limit,
            download_limit=body.download_limit,enabled=body.enabled)
    @app.patch('/api/sources/{source_id}')
    def edit_source(source_id:str,body:SourceEdit):
        fields=body.model_dump(exclude_none=True)
        return jobs.sources.update(source_id,**fields)
    @app.delete('/api/sources/{source_id}')
    def delete_source(source_id:str):
        with jobs.lock:
            if source_id in jobs.active_sources: raise HTTPException(409,'수집 중인 보드는 중지 후 삭제하세요.')
            jobs.sources.delete(source_id)
        return {'ok':True,'note':'수집 설정과 핀 작업 기록을 삭제했습니다. 저장된 이미지는 유지됩니다.'}
    @app.get('/api/sources/{source_id}/pins')
    def source_pins(source_id:str,limit:int=Query(100,ge=1,le=500)):
        return {'items':jobs.sources.pins(source_id,limit)}
    @app.post('/api/sources/{source_id}/run')
    def run_source(source_id:str): return jobs.submit('sync',{'source_id':source_id})
    @app.post('/api/sources/{source_id}/retry-errors')
    def retry_source(source_id:str):
        with jobs.lock:
            if source_id in jobs.active_sources: raise HTTPException(409,'수집 중인 보드입니다. 완료 후 재시도하세요.')
        changed=jobs.sources.retry_errors(source_id)
        if changed: jobs.submit('sync',{'source_id':source_id})
        return {'reset':changed}
    @app.post('/api/collect')
    def collect(body:CollectBody):
        if not body.permission_confirmed: raise ValueError('Pinterest 자동 접근의 사전 허가와 이미지 권한을 확인하세요.')
        target_url(body.target); return jobs.submit('collect',body.model_dump())
    @app.post('/api/login')
    def login(): return jobs.submit('login',{})
    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid:str):
        if not jobs.cancel(jid): raise HTTPException(409,'실행 또는 대기 중인 작업이 아닙니다.')
        return {'ok':True,'message':'중단을 요청했습니다. 현재 네트워크 요청 또는 추론이 끝나면 반영됩니다.'}
    @app.post('/api/jobs/{jid}/finish-login')
    def finish(jid:str):
        if not jobs.finish_login(jid): raise ValueError('실행 중인 로그인 작업이 아닙니다.')
        return {'ok':True}
    @app.post('/api/jobs/{jid}/retry')
    def retry(jid:str,body:Ids):
        j=store.job(jid)
        if not j: raise KeyError(jid)
        if not body.consent: raise ValueError('권한·비용을 확인하고 재실행을 승인하세요.')
        if j['status'] in ('queued','running'): raise HTTPException(409,'이미 실행 중입니다.')
        if j['kind']=='analyze': vision.ready()
        return jobs.submit(j['kind'],j['payload'])
    @app.post('/api/ai/analyze')
    def analyze(body:Ids):
        if not body.consent: raise ValueError('선택 이미지의 AI 전송·분석을 승인하세요.')
        vision.ready(); ids=body.ids or [p['id'] for p in store.candidates(untagged=True)][:50]
        if not ids: raise ValueError('분석할 이미지가 없습니다.')
        return jobs.submit('analyze',{'ids':list(dict.fromkeys(ids))})
    @app.post('/api/ai/rerank')
    def rerank(body:RankBody):
        if not body.consent: raise ValueError('AI 재정렬을 위한 이미지 전송을 승인하세요.')
        return vision.rerank(body.query,body.ids)
    @app.post('/api/index')
    def index(body:Ids):
        if not body.consent: raise ValueError('CLIP 모델 다운로드와 색인 생성을 승인하세요.')
        if not search.status()['installed']: raise SemanticUnavailable('04_install_semantic.bat으로 선택 모듈을 설치하세요.')
        with store.db() as c: indexed={r[0] for r in c.execute('SELECT pin_id FROM vectors WHERE model=?',(INDEX_MODEL,))}
        ids=body.ids or [p['id'] for p in store.candidates() if p['id'] not in indexed]
        if not ids: raise ValueError('모든 이미지가 이미 색인되어 있습니다.')
        return jobs.submit('index',{'ids':list(dict.fromkeys(ids))})
    @app.post('/api/settings')
    def settings(body:SettingsBody):
        if body.provider=='openai' and not body.openai_model.strip(): raise ValueError('모델 이름을 입력하세요.')
        store.save_settings(body.model_dump(exclude={'api_key'}))
        if body.api_key is not None: vision.set_key(body.api_key)
        return {'settings':store.settings(),'ai':vision.status()}
    @app.get('/api/ollama/models')
    def ollama_models():
        try:
            r=httpx.get('http://127.0.0.1:11434/api/tags',timeout=5,trust_env=False); r.raise_for_status()
            return {'models':[m['name'] for m in r.json().get('models',[])]}
        except Exception as e: raise HTTPException(503,'이 PC의 Ollama 서버에 연결할 수 없습니다.') from e
    @app.post('/api/export')
    def export(body:Ids):
        pins=[store.get(pid) for pid in dict.fromkeys(body.ids)]; pins=[p for p in pins if p]
        if not pins: raise ValueError('내보낼 이미지를 선택하세요.')
        if sum(p['byte_size'] for p in pins)>200*1024*1024: raise ValueError('한 번에 200 MB 이하로 나누어 내보내세요.')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            manifest=[]
            for p in pins:
                file=store.images/p['filename']
                if not file.exists(): raise ValueError('내보낼 이미지 파일이 없습니다. data 폴더를 확인하세요.')
                record={**p,'image':'images/'+p['filename']}; manifest.append(record); z.write(file,record['image'])
            z.writestr('manifest.json',json.dumps({'app':'Pin Archive','version':VERSION,'items':manifest},ensure_ascii=False,indent=2))
            z.writestr('README.txt','이미지와 원본 메타데이터의 내보내기입니다. 저작권이나 재사용 허가를 부여하지 않습니다. 원본 출처와 이용 조건을 확인하세요.')
        return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="pinarchive_export.zip"'})
    @app.get('/')
    def index_page(): return FileResponse(ROOT/'web/index.html')
    app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    return app
