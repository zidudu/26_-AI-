from __future__ import annotations
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from beta_helpers import app_env, client, USER, PASSWORD, PRIVATE, PUBLIC, LOCAL
from yme.events import JobEvents
from yme.library import Store
from yme.performance import make_mobile_preview, etag_matches, valid_derivative
from yme.remote_security import ServerConfig, https_origin, FAST_COOKIE, REMOTE_COOKIE
from connect_private_fast import private_command, public_443


def fixture_folder(path, index=0, video=False):
    stem=f'20260921_성능 테스트 {index:03d}'
    folder=path/stem;folder.mkdir(parents=True,exist_ok=True)
    (folder/(stem+'_metadata.json')).write_text(json.dumps({'title':f'성능 테스트 {index:03d}', 'channel':'테스트',
        'duration_seconds':2,'description':'본문'*6000, 'result':{'error':'작업로그'*2000}},ensure_ascii=False),encoding='utf-8')
    (folder/(stem+'_transcript.txt')).write_text('저장된 원문 '*3000,encoding='utf-8')
    if video:(folder/(stem+'.mp4')).write_bytes(b'fake video file'*100)
    return folder,stem


def test_compact_pagination_and_details(tmp_path):
    app=app_env(tmp_path);store=app.state.store
    for i in range(25):fixture_folder(tmp_path/'source',i)
    store.scan(tmp_path/'source');c=client(app)
    first=c.get('/api/items').json();second=c.get('/api/items?offset=20').json()
    assert len(first['items'])==20 and len(second['items'])==5 and first['total']==25
    assert not {r['id'] for r in first['items']} & {r['id'] for r in second['items']}
    assert 'description' not in first['items'][0]['metadata']
    full=store.listing(limit=200);small=store.listing(limit=20,compact=True)
    assert len(json.dumps(small)) < len(json.dumps(full))/10
    ident=first['items'][0]['id']
    assert 'description' in c.get('/api/items/'+ident).json()['metadata']
    assert 'description' not in c.get('/api/items/'+ident+'?compact=true').json()['metadata']


def test_conditional_response_requires_current_auth(tmp_path):
    app=app_env(tmp_path);folder,_=fixture_folder(tmp_path/'source');app.state.store.scan(folder);c=client(app)
    r=c.get('/api/items');etag=r.headers['etag']
    hit=c.get('/api/items',headers={'If-None-Match':etag});assert hit.status_code==304 and hit.content==b''
    assert 'private' in hit.headers['cache-control'] and 'must-revalidate' in hit.headers['cache-control']
    item=r.json()['items'][0]
    c.put('/api/items/'+item['id']+'/favorite',json={'value':True})
    assert c.get('/api/items',headers={'If-None-Match':etag}).status_code==200
    assert c.post('/api/auth/logout',json={}).status_code==200
    assert c.get('/api/items',headers={'If-None-Match':etag}).status_code==401


def test_etag_comparison_is_exact():
    assert etag_matches('W/"abc", "def"','"abc"')
    assert not etag_matches('"abcdef"','"abc"')


def test_thumbnail_small_cached_authenticated_and_invalidated(tmp_path):
    app=app_env(tmp_path);folder,stem=fixture_folder(tmp_path/'source')
    img=folder/(stem+'_thumbnail.jpg');Image.effect_noise((1280,720),90).convert('RGB').save(img,quality=95)
    app.state.store.scan(folder);item=app.state.store.listing()['items'][0]
    fid=next(f['id'] for f in item['files'] if f['kind']=='thumbnail');c=client(app)
    url='/api/thumbnails/'+fid;r=c.get(url)
    assert r.status_code==200 and len(r.content)<img.stat().st_size/3
    import io
    with Image.open(io.BytesIO(r.content)) as thumb:assert thumb.width<=320 and thumb.height<=180
    etag=r.headers['etag'];assert c.get(url,headers={'If-None-Match':etag}).status_code==304
    Image.new('RGB',(1280,720),'red').save(img)
    fresh=c.get(url,headers={'If-None-Match':etag});assert fresh.status_code==200 and fresh.headers['etag']!=etag
    assert len(list((app.state.store.data_dir/'cache'/'thumbnails').glob('*.jpg')))==1
    assert client(app,login=False).get(url,headers={'If-None-Match':etag}).status_code==401


def test_job_summary_never_contains_logs_or_payload(tmp_path):
    app=app_env(tmp_path);m=app.state.manager
    ident=m.enqueue('extract',{'text':'https://youtu.be/eu5H0iRJsJ4','settings':{'private-note':'note'}})
    app.state.store.job_update(ident,logs='SECRET-LOG'*2400,result={'huge':'nested'*5000})
    m.notify();c=client(app)
    r=c.get('/api/jobs/summary');row=r.json()['jobs'][0]
    assert not {'logs','result','payload'} & row.keys()
    assert len(r.content)<1500
    full=c.get('/api/jobs/'+ident).json();assert 'SECRET-LOG' in full['logs']
    assert client(app,login=False).get('/api/jobs/'+ident).status_code==401


def test_sse_hub_coalesces_does_not_queue_progress_backlog():
    async def scenario():
        hub=JobEvents();q=hub.subscribe();initial=await q.get();assert initial['jobs']==[]
        for i in range(100):hub.publish([{'progress':i}])
        await asyncio.sleep(.02)
        assert q.qsize()==1
        last=await q.get();assert last['jobs'][0]['progress']==99
        revision=last['revision'];hub.publish([{'progress':99}]);await asyncio.sleep(0)
        assert q.empty()
        hub.publish([{'progress':99}],True);await asyncio.sleep(.01)
        packet=await q.get();assert packet['library_revision']==1 and packet['revision']==revision+1
        hub.unsubscribe(q);assert hub.subscriber_count==0
        q=hub.subscribe();hub.close();await asyncio.sleep(.01);assert await q.get() is None
    asyncio.run(scenario())


def test_sse_subscriptions_are_bounded():
    async def scenario():
        hub=JobEvents();qs=[hub.subscribe() for _ in range(8)]
        with pytest.raises(ValueError):hub.subscribe()
        for q in qs:hub.unsubscribe(q)
    asyncio.run(scenario())


def test_mobile_job_validation_csrf_and_duplicate(tmp_path):
    app=app_env(tmp_path);folder,_=fixture_folder(tmp_path/'source',video=True);app.state.store.scan(folder)
    ident=app.state.store.listing()['items'][0]['id'];c=client(app)
    url='/api/items/'+ident+'/mobile-preview'
    assert c.post(url,json={'profile':'unsupported'}).status_code==400
    assert c.post(url,json={},headers={'X-YME-Token':'bad'}).status_code==403
    a=c.post(url,json={'profile':'mobile720'}).json();b=c.post(url,json={'profile':'mobile720'}).json()
    assert a['id']==b['id'] and b['reused_job']
    c2=c.post(url,json={'profile':'mobile480'}).json();assert c2['id']!=a['id']
    assert c.post('/api/jobs/mobile-batch',json={'item_ids':[ident]*21}).status_code==422


def test_config_migrates_and_fast_address_is_explicit(tmp_path):
    folder=tmp_path/'config';folder.mkdir()
    old={'port':8765,'private_url':PRIVATE,'data_dir':str(tmp_path/'data'),'output_dir':str(tmp_path/'output')}
    (folder/'server.json').write_text(json.dumps(old))
    cfg=ServerConfig.load(folder);assert cfg.fast_url==''
    cfg.fast_url=PRIVATE+':8443';cfg.save(folder);assert ServerConfig.load(folder).fast_url.endswith(':8443')
    for bad in [PRIVATE+':9443',PRIVATE,PRIVATE+':8443/x','https://evil.test:8443']:
        with pytest.raises(ValueError):https_origin(bad,fast=True)


def test_fast_and_public_cookie_sessions_do_not_clobber_each_other(tmp_path):
    app=app_env(tmp_path);cfg=app.state.remote_config;cfg.fast_url=PRIVATE+':8443';cfg.checked()
    c=client(app,PRIVATE)
    r=c.post(PRIVATE+':8443/auth/login',headers={'Origin':PRIVATE+':8443'},json={'username':USER,'password':PASSWORD,'remember':True})
    assert r.status_code==200 and FAST_COOKIE in r.headers['set-cookie']
    assert c.cookies.get(REMOTE_COOKIE) and c.cookies.get(FAST_COOKIE)
    for _ in range(2):
        assert c.get(PRIVATE+'/api/bootstrap',headers={'Origin':PRIVATE}).json()['connection_mode']=='tailscale'
        assert c.get(PRIVATE+':8443/api/bootstrap',headers={'Origin':PRIVATE+':8443'}).json()['connection_mode']=='tailscale_fast'
    assert c.get('/api/items',headers={'Host':'test-pc.example-tail.ts.net:9443'}).status_code==403


def test_fast_helper_does_not_reset_public_port():
    command=private_command('tailscale',8765)
    assert command==['tailscale','serve','--bg','--https=8443','http://127.0.0.1:8765']
    before={'TCP':{'443':{'HTTPS':True}},'Web':{'host.ts.net:443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:8765'}}}},'AllowFunnel':{'host.ts.net:443':True}}
    after=json.loads(json.dumps(before));after['TCP']['8443']={'HTTPS':True};after['Web']['host.ts.net:8443']={'Handlers':{}}
    assert public_443(before,'host.ts.net')==public_443(after,'host.ts.net')


@pytest.mark.parametrize('profile,kind,height',[('mobile720','mobile',720),('mobile480','economy',480)])
def test_real_mobile_encode_reuse_range_and_rescan(tmp_path,profile,kind,height):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg/ffprobe not installed')
    app=app_env(tmp_path);folder,stem=fixture_folder(tmp_path/'한글 경로')
    source=folder/(stem+'.mp4')
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=60','-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
        '-t','1.6','-c:v','libx264','-threads','2','-preset','ultrafast','-crf','18','-c:a','aac','-y',str(source)],check=True)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    store=app.state.store;ident=store.register_folder(folder)[0]
    result=make_mobile_preview(ident,store,profile)
    f=store.file(result['file_id']);assert f and f['kind']==kind
    out=Path(f['path']);assert out.stat().st_size<source.stat().st_size
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(out)]))
    v=next(s for s in info['streams'] if s['codec_type']=='video')
    a=next(s for s in info['streams'] if s['codec_type']=='audio')
    assert v['codec_name']=='h264' and v['height']<=height and v['pix_fmt']=='yuv420p'
    assert v['r_frame_rate']=='30/1' and a['codec_name']=='aac'
    raw=out.read_bytes();assert raw.index(b'moov')<raw.index(b'mdat')
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    assert make_mobile_preview(ident,store,profile)['reused'] is True
    store.scan(folder);assert store.file(f['id'])
    c=client(app);r=c.get('/media/'+f['id'],headers={'Range':'bytes=0-31'})
    assert r.status_code==206 and r.content==raw[:32]
    assert client(app,PUBLIC).get('/media/'+f['id']).status_code==403
    assert client(app,login=False).get('/media/'+f['id']).status_code==401
    stat=source.stat();os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+10_000_000))
    assert not valid_derivative(store,f['id']) and store.file(f['id']) is None


def test_session_heartbeat_validation_does_not_extend_idle(tmp_path):
    app=app_env(tmp_path);c=client(app)
    import time
    from yme.remote_security import LOCAL_COOKIE
    token=c.cookies.get(LOCAL_COOKIE)
    with app.state.security.connect() as conn:conn.execute('UPDATE sessions SET seen=?',(time.time()-60,))
    before=app.state.security.session(token,LOCAL,False)['seen']
    assert app.state.security.session(token,LOCAL,False)['seen']==before
    with app.state.security.connect() as conn:conn.execute('UPDATE sessions SET seen=?',(time.time()-4000,))
    assert app.state.security.session(token,LOCAL,False) is None


def test_legacy_serve_setup_refuses_to_replace_public_443(tmp_path, monkeypatch):
    import connect_remote as remote
    from types import SimpleNamespace
    cfg=ServerConfig(data_dir=str(tmp_path/'data'),output_dir=str(tmp_path/'output'))
    config=tmp_path/'config';cfg.save(config)
    monkeypatch.setattr(remote,'CONFIG',config)
    monkeypatch.setattr(remote,'tailscale_exe',lambda:'tailscale')
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        if cmd[1:]==['status','--json']:
            return SimpleNamespace(returncode=0,stdout=json.dumps({'BackendState':'Running','Self':{'DNSName':'test-pc.example-tail.ts.net.'}}).encode())
        if cmd[1:]==['serve','status','--json']:
            return SimpleNamespace(returncode=0,stdout=b'{"AllowFunnel":{"test-pc.example-tail.ts.net:443":true}}')
        raise AssertionError('unexpected modifying command '+str(cmd))
    monkeypatch.setattr(remote.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='05_connect_private_fast'):
        remote.tailscale_setup()
    assert all('--bg' not in cmd and 'reset' not in cmd for cmd in calls)


def test_sse_updates_keep_api_and_cache_private(tmp_path):
    app=app_env(tmp_path);c=client(app)
    r=c.get('/api/jobs/summary');assert r.status_code==200
    assert r.headers['cache-control']=='private, no-cache, must-revalidate'
    etag=r.headers['etag'];c.post('/api/tags',json={'name':'SSE revision'})
    r2=c.get('/api/jobs/summary',headers={'If-None-Match':etag})
    assert r2.status_code==200 and r2.json()['library_revision']>r.json()['library_revision']
    c.post('/api/auth/logout',json={})
    assert c.get('/api/jobs/summary',headers={'If-None-Match':r2.headers['etag']}).status_code==401
