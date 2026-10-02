"""V8 로컬 서버: HTML GUI, 파일 열람, 태그, 다운로드 대기열 API."""
from __future__ import annotations
import asyncio
import time
import importlib.metadata
import importlib.util
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse, StreamingResponse, Response
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from yme import VERSION
from yme.engine import DEFAULTS, parse_sources, validate_settings
from yme.library import BASE, Store, TIME_RE, read_text
from yme.tasks import Manager, TERMINAL
from yme.remote_security import ServerConfig, Security, require_local
from yme.performance import ThumbnailCache, conditional_json, conditional_file, PROFILES


class ExtractInput(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    settings: dict = Field(default_factory=dict)

class ImportInput(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)

class TagInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)

class TagsInput(BaseModel):
    names: list[str] = Field(default_factory=list, max_length=30)

class MobileInput(BaseModel):
    profile: str = 'mobile720'

class MobileBatchInput(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=20)
    profile: str = 'mobile720'

class FavoriteInput(BaseModel):
    value: bool


def create_app(data_dir=None, output_dir=None, run_jobs=True, config=None, security_dir=None):
    security_dir = Path(security_dir or BASE / 'config').resolve()
    config = config or ServerConfig.load(security_dir)
    if data_dir:
        config.data_dir = str(Path(data_dir).resolve())
    if output_dir:
        config.output_dir = str(Path(output_dir).resolve())
    config.checked()
    security = Security(config, security_dir)
    store = Store(config.data_dir)
    output = Path(config.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manager = Manager(store)
    thumbnails = ThumbnailCache(store.data_dir / "cache" / "thumbnails")

    @asynccontextmanager
    async def lifespan(app):
        if run_jobs:
            manager.start()
        yield
        if run_jobs:
            manager.close()

    app = FastAPI(title='YouTube Media Library V8 beta.2 Performance', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager
    security.install(app)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.get('/')
    def home():
        return FileResponse(BASE / 'static/index.html', media_type='text/html')

    @app.get('/api/health')
    def health():
        return {'application': 'youtube-media-library-beta', 'version': VERSION, 'status': 'ok'}

    @app.get('/api/bootstrap')
    def bootstrap(request: Request):
        versions = {}
        for name in ('fastapi', 'uvicorn', 'yt-dlp', 'yt-dlp-ejs', 'youtube-transcript-api', 'imageio-ffmpeg'):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        mode = request.state.connection_mode
        return {'token': request.state.auth_session['csrf'], 'version': VERSION, 'defaults': DEFAULTS,
                'output': str(output), 'data_dir': str(store.data_dir), 'roots': store.roots(),
                'versions': versions, 'runtimes': [n for n in ('deno', 'node', 'qjs') if shutil.which(n)],
                'free_gb': round(shutil.disk_usage(output).free / 1024**3, 1),
                'remote': mode != 'local', 'desktop_actions': mode == 'local' and os.environ.get('YME_SERVICE_MODE') != '1', 'connection_mode': mode, 'can_stream': mode != 'cloudflare',
                'private_url': config.private_url, 'cloudflare_url': config.cloudflare_url,
                'fast_url': config.fast_url, 'page_size': 20, 'job_transport': 'sse',
                'mobile_profiles': PROFILES,
                'username': security.read_owner()['username'],
                'uptime_seconds': int(__import__('time').monotonic() - security.started),
                'session_hours': config.session_hours, 'idle_minutes': config.idle_minutes, 'remember_days': 30}

    @app.get('/api/items')
    def items(request: Request, q: str = '', kind: str = '', tags: str = '', favorite: bool = False,
              untagged: bool = False, offset: int = 0, limit: int = 20, sort: str = 'new'):
        ids = [int(t) for t in tags.split(',') if t.strip()]
        value = store.listing(q[:200], kind, ids[:30], favorite, untagged, max(0, offset), min(100, max(1, limit)), sort, compact=True)
        return conditional_json(request, value)

    @app.get('/api/items/{item_id}')
    def item(item_id: str, request: Request, compact: bool = False):
        value = store.item(item_id)
        if not value:
            raise HTTPException(404, '항목을 찾지 못했습니다.')
        if compact:
            value['metadata'] = {k: value['metadata'].get(k) for k in ('duration_seconds', 'playlist')}
        return conditional_json(request, value)

    @app.put('/api/items/{item_id}/tags')
    def item_tags(item_id: str, body: TagsInput):
        if not store.item(item_id):
            raise HTTPException(404, '항목을 찾지 못했습니다.')
        store.assign_tags(item_id, body.names, replace=True)
        manager.notify(library_changed=True)
        return {'ok': True}

    @app.put('/api/items/{item_id}/favorite')
    def favorite(item_id: str, body: FavoriteInput):
        store.set_favorite(item_id, body.value)
        manager.notify(library_changed=True)
        return {'ok': True}

    @app.get('/api/tags')
    def tags(request: Request):
        return conditional_json(request, store.tags())

    @app.post('/api/tags')
    def add_tag(body: TagInput):
        ident = store.create_tag(body.name)
        manager.notify(library_changed=True)
        return {'id': ident}

    @app.put('/api/tags/{tag_id}')
    def rename_tag(tag_id: int, body: TagInput):
        store.rename_tag(tag_id, body.name)
        manager.notify(library_changed=True)
        return {'ok': True}

    @app.delete('/api/tags/{tag_id}')
    def delete_tag(tag_id: int):
        store.delete_tag(tag_id)
        manager.notify(library_changed=True)
        return {'ok': True}

    @app.api_route('/media/{file_id}', methods=['GET', 'HEAD'])
    def media(file_id: str, request: Request, download: bool = False):
        f = store.file(file_id)
        if not f:
            raise HTTPException(404, '파일을 찾지 못했습니다. 파일을 이동했다면 폴더를 다시 가져와 주세요.')
        if request.state.connection_mode == 'cloudflare':
            if f['kind'] in {'video', 'audio', 'preview', 'mobile', 'economy'} or f['size'] > 5_000_000:
                raise HTTPException(403, 'Cloudflare 관리 주소에서는 영상·음원·대용량 전송을 사용하지 않습니다. 개인 접속 주소로 열어 주세요.')
        return FileResponse(f['path'], media_type=f['mime'], filename=f['name'],
                            content_disposition_type='attachment' if download else 'inline')

    @app.get('/api/text/{file_id}')
    def text(file_id: str, request: Request):
        f = store.file(file_id)
        if not f or f['kind'] not in {'transcript', 'timestamp', 'metadata', 'segments'}:
            raise HTTPException(404, '텍스트 파일을 찾지 못했습니다.')
        content = read_text(Path(f['path']))
        cues = []
        if f['kind'] == 'timestamp':
            for line in content.splitlines():
                m = TIME_RE.match(line)
                if m:
                    h, minute, sec, fraction, words = m.groups()
                    cues.append({'start': int(h)*3600 + int(minute)*60 + int(sec) + float('0.' + (fraction or '0')), 'text': words})
        return conditional_json(request, {'name': f['name'], 'text': content, 'cues': cues, 'truncated': f['size'] > 2_000_000})

    @app.post('/api/sources/validate')
    def sources(body: ExtractInput):
        return parse_sources(body.text)

    @app.post('/api/jobs/extract')
    def extract(body: ExtractInput):
        parsed = parse_sources(body.text)
        if not parsed['sources']:
            raise ValueError('사용 가능한 YouTube URL을 찾지 못했습니다.')
        settings = validate_settings(body.settings)
        return {'id': manager.enqueue('extract', {'text': body.text, 'settings': settings, 'output': str(output)}), 'validation': parsed}

    @app.post('/api/jobs/import')
    def import_root(body: ImportInput, request: Request):
        require_local(request)
        p = Path(body.path.strip().strip('"')).expanduser().resolve()
        if not p.is_dir():
            raise ValueError('폴더 경로가 존재하지 않습니다. V7의 output 폴더를 선택해 주세요.')
        if str(p) == p.anchor:
            raise ValueError('드라이브 전체 대신 결과 폴더를 선택해 주세요.')
        names = [Store.tag_name(t)[0] for t in body.tags]
        return {'id': manager.enqueue('import', {'path': str(p), 'tags': names})}

    @app.post('/api/jobs/rescan')
    def rescan():
        roots = sorted(set([str(output), *store.roots()]))
        return {'id': manager.enqueue('rescan', {'roots': roots})}

    @app.post('/api/items/{item_id}/preview')
    def preview(item_id: str):
        x = store.item(item_id)
        if not x or not any(f['kind'] == 'video' for f in x['files']):
            raise ValueError('변환할 영상 파일이 없습니다.')
        for j in store.jobs():
            if j['kind'] == 'preview' and j['payload'].get('item_id') == item_id and j['status'] not in TERMINAL:
                return {'id': j['id']}
        return {'id': manager.enqueue('preview', {'item_id': item_id})}

    @app.api_route('/api/thumbnails/{file_id}', methods=['GET', 'HEAD'])
    def thumbnail(file_id: str, request: Request):
        f = store.file(file_id)
        if not f or f['kind'] != 'thumbnail':
            raise HTTPException(404, '썸네일을 찾지 못했습니다.')
        try:
            p = thumbnails.get(Path(f['path']), file_id)
        except (OSError, ValueError) as exc:
            raise HTTPException(422, '썸네일 변환에 실패했습니다.') from exc
        return conditional_file(request, p, 'image/jpeg')

    def enqueue_mobile(ids, profile):
        if profile not in PROFILES:
            raise ValueError('지원하지 않는 모바일 재생본 형식입니다.')
        ids = list(dict.fromkeys(ids))
        for ident in ids:
            value = store.item(ident)
            if not value or not any(f['kind'] == 'video' for f in value['files']):
                raise ValueError('원본 영상이 없는 항목입니다.')
        # Protect check + enqueue as one operation for double taps/concurrent clients.
        with manager.lock:
            pending = [j for j in store.jobs() if j['status'] not in TERMINAL]
            duplicate = []
            remaining = []
            for ident in ids:
                found = next((j for j in pending if j['kind'] in {'mobile_preview','mobile_batch'}
                              and j['payload'].get('profile','mobile720') == profile
                              and ident in (j['payload'].get('item_ids') or [j['payload'].get('item_id')])), None)
                if found:
                    duplicate.append(found['id'])
                else:
                    remaining.append(ident)
            if not remaining:
                return {'id': duplicate[0], 'reused_job': True}
            kind = 'mobile_preview' if len(remaining) == 1 else 'mobile_batch'
            payload = {'profile': profile, 'item_ids': remaining}
            if len(remaining) == 1:
                payload['item_id'] = remaining[0]
            return {'id': manager.enqueue(kind, payload), 'count': len(remaining)}

    @app.post('/api/items/{item_id}/mobile-preview')
    def mobile_preview(item_id: str, body: MobileInput):
        return enqueue_mobile([item_id], body.profile)

    @app.post('/api/jobs/mobile-batch')
    def mobile_batch(body: MobileBatchInput):
        return enqueue_mobile(body.item_ids, body.profile)

    @app.get('/api/jobs/summary')
    def job_summary(request: Request):
        return conditional_json(request, manager.events.snapshot())

    @app.get('/api/events')
    async def events(request: Request):
        # Same-origin HttpOnly session is required by existing middleware.
        # Idle SSE heartbeats do not extend the session's inactivity timer.
        if manager.events.subscriber_count >= 8:
            raise HTTPException(429, '실시간 연결이 많습니다. 다른 탭을 닫아 주세요.')
        origin = request.state.effective_origin
        cookie = request.cookies.get(request.state.cookie_name, '')
        queue = manager.events.subscribe()
        async def stream():
            try:
                yield 'retry: 5000\n\n'
                while not await request.is_disconnected():
                    if not await run_in_threadpool(security.session, cookie, origin, False):
                        yield 'event: auth_expired\ndata: {}\n\n'
                        return
                    try:
                        packet = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ': heartbeat\n\n'
                        continue
                    if packet is None:
                        return
                    # A logout during queue.wait must not expose the next event.
                    if not await run_in_threadpool(security.session, cookie, origin, False):
                        yield 'event: auth_expired\ndata: {}\n\n'
                        return
                    yield ('id: ' + str(packet['revision']) + '\nevent: jobs\ndata: '
                           + json.dumps(packet, ensure_ascii=False, separators=(',', ':')) + '\n\n')
            finally:
                manager.events.unsubscribe(queue)
        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @app.get('/api/jobs/{job_id}')
    def job_detail(job_id: str):
        value = store.job(job_id)
        if not value:
            raise HTTPException(404, '작업을 찾지 못했습니다.')
        return value

    @app.get('/api/jobs')
    def jobs():
        return store.jobs()

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        manager.cancel(job_id)
        return {'ok': True}

    @app.post('/api/jobs/{job_id}/retry')
    def retry(job_id: str, request: Request):
        found = next((j for j in store.jobs() if j['id'] == job_id), None)
        if not found:
            raise ValueError('작업을 찾지 못했습니다.')
        if found['kind'] == 'import':
            require_local(request)
        if found['status'] not in TERMINAL:
            raise ValueError('진행 중인 작업입니다.')
        return {'id': manager.enqueue(found['kind'], found['payload'])}

    @app.post('/api/choose-folder')
    def choose_folder(request: Request):
        require_local(request)
        if os.environ.get("YME_SERVICE_MODE") == "1":
            raise ValueError("백그라운드 실행 중에는 폴더 경로를 직접 입력해 주세요.")
        if os.name != 'nt':
            raise ValueError('폴더 선택 창은 Windows용입니다. 아래 경로 입력칸을 사용해 주세요.')
        try:
            p = subprocess.run([sys.executable, str(BASE / 'folder_dialog.py')], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
            if p.returncode:
                raise RuntimeError(p.stderr.decode('utf-8', 'replace')[-800:])
            return json.loads(p.stdout.decode('utf-8', 'replace'))
        except Exception as exc:
            raise ValueError('폴더 선택 창을 열지 못했습니다. 탐색기의 주소를 경로 입력칸에 붙여넣어 주세요. ' + str(exc)) from None

    @app.post('/api/items/{item_id}/open-folder')
    def open_folder(item_id: str, request: Request):
        require_local(request)
        if os.environ.get("YME_SERVICE_MODE") == "1":
            raise ValueError("백그라운드 실행 중에는 탐색기를 열 수 없습니다. 표시된 경로를 이용하세요.")
        x = store.item(item_id)
        if not x or not Path(x['folder']).is_dir():
            raise ValueError('저장 폴더를 찾지 못했습니다.')
        if os.name != 'nt':
            raise ValueError('탐색기 열기는 Windows에서 지원합니다. 경로: ' + x['folder'])
        os.startfile(x['folder'])
        return {'ok': True}

    @app.post('/api/files/{file_id}/open')
    def open_file(file_id: str, request: Request):
        require_local(request)
        if os.environ.get("YME_SERVICE_MODE") == "1":
            raise ValueError("백그라운드 실행 중에는 브라우저 보기나 다운로드 버튼을 사용하세요.")
        f = store.file(file_id)
        if not f:
            raise ValueError('파일을 찾지 못했습니다.')
        if os.name != 'nt':
            raise ValueError('기본 앱 열기는 Windows에서 지원합니다. 다운로드 버튼을 사용해 주세요.')
        os.startfile(f['path'])
        return {'ok': True}

    app.mount('/static', StaticFiles(directory=BASE / 'static'), name='static')
    return app
