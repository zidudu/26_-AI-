from pathlib import Path
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app import create_app
from yme.engine import parse_sources, normalize_url, choose_transcript, format_selector, safe_name, convert_audio, validate_settings
from yme.library import Store, classify
from beta_helpers import app_env,client as beta_client


def sample(root, name='20260919_한글 제목', vid='eu5H0iRJsJ4', title='한글 <script> 제목'):
    folder=root/name
    folder.mkdir(parents=True,exist_ok=True)
    meta={'title':title,'channel':'테스트 채널','video_id':vid,'saved_at':'2026-09-19T12:00:00+09:00'}
    (folder/(name+'_metadata.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
    (folder/(name+'_transcript.txt')).write_text('유튜브 제목 : '+title+'\n[자막 본문]\n안녕하세요\n두 번째 문장',encoding='utf-8-sig')
    (folder/(name+'_timestamp.txt')).write_text('[00:00:00] 안녕하세요\n[00:00:02] 두 번째 문장',encoding='utf-8-sig')
    (folder/(name+'.mp4')).write_bytes(b'0123456789abcdef'*30)
    return folder


@pytest.fixture
def env(tmp_path):
    source=tmp_path/'source'
    sample(source)
    app=app_env(tmp_path)
    store=app.state.store
    store.scan(source)
    client=beta_client(app)
    token=client.get('/api/bootstrap').json()['token']
    client.headers['X-YME-Token']=token
    return client,store,source


def test_url_variants():
    for url in ['eu5H0iRJsJ4','https://youtu.be/eu5H0iRJsJ4?si=xx','https://www.youtube.com/watch?v=eu5H0iRJsJ4&list=PLaa',
                'https://www.youtube.com/shorts/eu5H0iRJsJ4','https://m.youtube.com/live/eu5H0iRJsJ4']:
        assert normalize_url(url)[2]=='eu5H0iRJsJ4'
    assert normalize_url('https://youtu.be/-vzwdo_qsAY')[2]=='-vzwdo_qsAY'


@pytest.mark.parametrize('bad',['https://evil-youtube.com/watch?v=eu5H0iRJsJ4','file:///etc/passwd','https://youtube.com@evil.test/watch?v=eu5H0iRJsJ4','https://youtube.com:9999/watch?v=eu5H0iRJsJ4','not-a-url','https://youtube.com/channel/anything'])
def test_reject_non_youtube(bad):
    with pytest.raises(ValueError):normalize_url(bad)


def test_dedup_and_playlist():
    x=parse_sources('https://youtu.be/eu5H0iRJsJ4\nhttps://www.youtube.com/watch?v=eu5H0iRJsJ4\nhttps://youtube.com/playlist?list=PL123456789012345\nbad')
    assert len(x['sources'])==2 and len(x['duplicates'])==1 and len(x['errors'])==1
    assert x['sources'][1]['kind']=='playlist'


def test_language():
    data=[SimpleNamespace(language_code='en',is_generated=False),SimpleNamespace(language_code='ko',is_generated=True),SimpleNamespace(language_code='ko',is_generated=False)]
    assert choose_transcript(data,'auto') is data[2]
    assert choose_transcript(data,'en') is data[0]
    with pytest.raises(RuntimeError):choose_transcript(data,'fr')


def test_quality_cap_and_names():
    selector=format_selector('720')
    assert '[height<=720]' in selector and not selector.endswith('/b')
    assert format_selector('best')=='bv+ba/b'
    assert safe_name('한글:제목?/')=='한글_제목__'
    assert safe_name('CON.txt').startswith('_')
    with pytest.raises(ValueError):validate_settings({'playlist_limit':1001})


def test_old_generic_names(tmp_path):
    folder=tmp_path/'20260919_기존 영상';folder.mkdir()
    assert classify(folder/'transcript.txt')==(folder.name,'transcript')
    assert classify(folder/'transcript_timestamp.txt')==(folder.name,'timestamp')


def test_home_and_security(env):
    c,s,_=env
    assert c.get('/').status_code==200
    assert c.get('/').headers['x-frame-options']=='DENY'
    assert c.get('/static/app.js').status_code==200
    assert c.post('/api/tags',json={'name':'태그'},headers={'X-YME-Token':'bad'}).status_code==403
    assert c.get('/api/items',headers={'Origin':'https://evil.test'}).status_code==403
    assert c.get('/api/items',headers={'Host':'evil.test:8765'}).status_code==403
    assert c.get('/media/../../etc/passwd').status_code!=200


def test_range(env):
    c,s,_=env
    f=next(f for f in s.listing()['items'][0]['files'] if f['kind']=='video')
    r=c.get('/media/'+f['id'],headers={'Range':'bytes=0-9'})
    assert r.status_code==206 and r.content==b'0123456789'
    assert r.headers['content-range'].startswith('bytes 0-9/')
    assert c.get('/media/'+f['id'],headers={'Range':'bytes=999999-'}).status_code==416
    assert c.head('/media/'+f['id']).status_code==200


def test_tags_persist_and_filters(env):
    c,s,root=env
    item=s.listing()['items'][0]
    r=c.put('/api/items/'+item['id']+'/tags',json={'names':['AI 공부','음악']})
    assert r.status_code==200
    tags=c.get('/api/tags').json();assert len(tags)==2
    assert c.get('/api/items',params={'tags':str(tags[0]['id'])}).json()['total']==1
    assert c.get('/api/items',params={'q':'두 번째'}).json()['total']==1
    assert c.get('/api/items',params={'untagged':'true'}).json()['total']==0
    s.scan(root)
    again=Store(s.data_dir)
    assert len(again.item(item['id'])['tags'])==2
    c.put('/api/tags/'+str(tags[0]['id']),json={'name':'새 이름'})
    assert '새 이름' in [t['name'] for t in again.tags()]
    c.delete('/api/tags/'+str(tags[0]['id']))
    assert len(again.item(item['id'])['tags'])==1


def test_tag_update_is_atomic_and_deduplicated(env):
    c,s,_=env
    item_id=s.listing()['items'][0]['id']
    assert c.put('/api/items/'+item_id+'/tags',json={'names':['기존']}).status_code==200
    bad=c.put('/api/items/'+item_id+'/tags',json={'names':['새 태그','x'*41]})
    assert bad.status_code==400
    assert [t['name'] for t in s.item(item_id)['tags']]==['기존']
    assert [t['name'] for t in s.tags()]==['기존']
    ok=c.put('/api/items/'+item_id+'/tags',json={'names':['AI 공부','ai 공부','#AI 공부']})
    assert ok.status_code==200
    assert [t['name'] for t in s.item(item_id)['tags']]==['AI 공부']
    assert c.put('/api/tags/999999',json={'name':'없는 태그'}).status_code==400
    assert c.delete('/api/tags/999999').status_code==400


def test_tag_injection_is_plain_data(env):
    c,s,_=env
    tag='<img src=x onerror=alert(1)>'
    assert c.post('/api/tags',json={'name':tag}).status_code==200
    assert tag in [t['name'] for t in c.get('/api/tags').json()]


def test_import_does_not_write_source(env):
    c,s,root=env
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    s.scan(root,['테스트'])
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert s.listing()['total']==1


def test_timestamp_text(env):
    c,s,_=env
    f=next(f for f in s.listing()['items'][0]['files'] if f['kind']=='timestamp')
    data=c.get('/api/text/'+f['id']).json()
    assert data['cues']==[{'start':0.0,'text':'안녕하세요'},{'start':2.0,'text':'두 번째 문장'}]


def test_favorite(env):
    c,s,_=env
    ident=s.listing()['items'][0]['id']
    assert c.put(f'/api/items/{ident}/favorite',json={'value':True}).status_code==200
    assert c.get('/api/items?favorite=true').json()['total']==1


def test_job_queue_cancel_invalid(env):
    c,s,_=env
    assert c.post('/api/jobs/extract',json={'text':'bad'}).status_code==400
    j=c.post('/api/jobs/extract',json={'text':'https://youtu.be/eu5H0iRJsJ4','settings':{'mode':'subtitles'}}).json()
    assert s.jobs()[0]['status']=='queued'
    c.post('/api/jobs/'+j['id']+'/cancel',json={})
    assert s.jobs()[0]['status']=='cancelled'
    c.post('/api/jobs/'+j['id']+'/retry',json={})
    assert len(s.jobs())==2


def test_metadata_path_not_followed(tmp_path):
    root=tmp_path/'source';folder=sample(root)
    p=next(folder.glob('*_metadata.json'))
    data=json.loads(p.read_text(encoding='utf-8'));data['result']={'video':{'file':'../../secret.txt'}};p.write_text(json.dumps(data),encoding='utf-8')
    s=Store(tmp_path/'db');s.scan(root)
    assert all('secret' not in f['name'] for f in s.listing()['items'][0]['files'])


def test_missing_file_is_404(env):
    c,s,_=env
    f=s.listing()['items'][0]['files'][0]
    Path(s.file(f['id'])['path']).unlink()
    assert c.get('/media/'+f['id']).status_code==404


def test_real_worker_import(tmp_path):
    from yme.tasks import Manager
    root=tmp_path/'source';sample(root)
    s=Store(tmp_path/'db');manager=Manager(s);manager.start()
    try:
        jid=manager.enqueue('import',{'path':str(root),'tags':['실행 검증']})
        for _ in range(100):
            state=next(j for j in s.jobs() if j['id']==jid)
            if state['status'] in {'success','failed'}:break
            time.sleep(.1)
        assert state['status']=='success',state
        assert s.listing()['total']==1 and s.tags()[0]['name']=='실행 검증'
    finally:manager.close()


def test_ffmpeg_korean_path_binary_logs(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg not available')
    folder=tmp_path/'한글 (폴더)';folder.mkdir()
    source=folder/'원본.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=black:s=160x90:r=10','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','1.5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
    target=folder/'음원.m4a'
    assert convert_audio(source,target,'m4a')=='stream_copy'
    assert target.stat().st_size>1000


def test_extraction_pipeline_partial_success_and_reuse(tmp_path, monkeypatch):
    """YouTube boundary is mocked; real local media, FFmpeg, files and DB are exercised."""
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not available')
    from yme import engine
    source = tmp_path/'테스트 원본.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','lavfi','-i','color=c=black:s=160x90:r=10',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=44100',
        '-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)], check=True)
    monkeypatch.setattr(engine, 'info_for', lambda job: {
        'title':'동일: 제목?', 'channel':'검증', 'duration_seconds':1,
        'channel_url':'https://youtube.com/@test'})
    selected = SimpleNamespace(language_code='ko', language='Korean', is_generated=False)
    def subtitles(vid, policy):
        if vid == '-vzwdo_qsAY':
            raise RuntimeError('테스트용 자막 없음')
        return selected, [{'start':0, 'duration':1.0, 'text':'문장\n두 줄을 유지합니다.'}]
    monkeypatch.setattr(engine, 'fetch_subtitles', subtitles)
    downloads = []
    def download(job, work, video, quality, hook):
        downloads.append(job['id'])
        path = work/'video.mp4'
        shutil.copyfile(source, path)
        return path
    monkeypatch.setattr(engine, 'download_media', download)
    store = Store(tmp_path/'db')
    payload = {'text':'eu5H0iRJsJ4\n-vzwdo_qsAY', 'output':str(tmp_path/'output'),
        'settings':{'mode':'all', 'quality':'720', 'audio_format':'m4a', 'thumbnail':False, 'tags':['검증 자료']}}
    result = engine.extract_batch(payload, store)
    assert (result['success'], result['partial'], result['failed']) == (1,1,0)
    assert len(downloads) == 2
    listing = store.listing()
    assert listing['total'] == 2
    assert len({i['folder'] for i in listing['items']}) == 2  # same title, different IDs
    for item in listing['items']:
        assert {'video','audio','metadata'} <= {f['kind'] for f in item['files']}
        assert item['tags'][0]['name'] == '검증 자료'
        assert item['metadata']['result']['audio']['method'] == 'stream_copy'
        if item['video_id']=='eu5H0iRJsJ4':
            f=next(f for f in item['files'] if f['kind']=='transcript')
            assert '문장\n두 줄을 유지합니다.' in Path(store.file(f['id'])['path']).read_text(encoding='utf-8-sig')
    # Rerun preserves files and reuses completed video/audio, not a partial download.
    engine.extract_batch(payload, store)
    assert len(downloads) == 2
    assert store.listing()['total'] == 2


def test_real_preview_and_rescan_retains_cache(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not available')
    from yme.engine import make_preview
    root=tmp_path/'source';folder=sample(root)
    video=next(folder.glob('*.mp4'))
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi',
        '-i','color=c=black:s=160x90:r=10','-t','1', '-c:v','libx264',str(video)],check=True)
    original=video.read_bytes()
    store=Store(tmp_path/'db');store.scan(root)
    ident=store.listing()['items'][0]['id']
    output=make_preview(ident,store)
    assert output['file_id']
    assert any(f['kind']=='preview' for f in store.item(ident)['files'])
    assert video.read_bytes()==original
    store.scan(root)
    assert any(f['kind']=='preview' for f in store.item(ident)['files'])


def test_server_restart_marks_old_job_interrupted(tmp_path):
    from yme.tasks import Manager
    store=Store(tmp_path/'db')
    manager=Manager(store)
    ident=manager.enqueue('import',{'path':str(tmp_path/'source')})
    with store.connect() as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=?",(ident,))
    restarted=Manager(store)
    restarted.start()
    try:
        assert next(j for j in store.jobs() if j['id']==ident)['status']=='interrupted'
    finally:
        restarted.close()
