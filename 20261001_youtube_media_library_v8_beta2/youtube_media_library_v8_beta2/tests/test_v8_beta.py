from pathlib import Path
import json
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from app import create_app
from beta_helpers import app_env,client,USER,PASSWORD,LOCAL,PRIVATE,PUBLIC
from yme.remote_security import ServerConfig,https_origin,set_owner,REMOTE_COOKIE,LOCAL_COOKIE
from yme.library import Store
from configure_beta import migrate_library


def add_sample(app,path):
    folder=path/'media';folder.mkdir()
    stem='20260919_테스트 자료'
    (folder/(stem+'.mp4')).write_bytes(b'0123456789'*100)
    (folder/(stem+'.m4a')).write_bytes(b'audio'*100)
    (folder/(stem+'_transcript.txt')).write_text('원문 테스트',encoding='utf-8')
    app.state.store.scan(folder)
    return app.state.store.listing()['items'][0]


def test_requires_owner(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(config=ServerConfig(),security_dir=tmp_path,run_jobs=False)


@pytest.mark.parametrize('origin',[LOCAL,PRIVATE,PUBLIC])
def test_auth_required_everywhere(tmp_path,origin):
    app=app_env(tmp_path);c=client(app,origin,False);item=add_sample(app,tmp_path)
    assert c.get('/',follow_redirects=False).status_code==303
    assert c.get('/login').status_code==200
    for path in ['/api/bootstrap','/api/items','/api/tags','/api/jobs','/static/app.js']:
        assert c.get(path).status_code==401
    for f in item['files']:
        for method in ('get','head'):
            assert getattr(c,method)('/media/'+f['id'],headers={'Range':'bytes=0-9'}).status_code==401
    public=c.get('/api/health').json()
    assert not any(x in public for x in ('token','data_dir','lan_pin','username','output'))


@pytest.mark.parametrize('origin',[LOCAL,PRIVATE,PUBLIC])
def test_cookie_flags_and_authenticated_views(tmp_path,origin):
    app=app_env(tmp_path);c=client(app,origin,False)
    r=c.post('/auth/login',json={'username':USER,'password':PASSWORD})
    assert r.status_code==200
    cookie=r.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/' in cookie
    if origin!=LOCAL:assert 'Secure' in cookie and REMOTE_COOKIE in cookie
    else:assert LOCAL_COOKIE in cookie and '; Secure' not in cookie
    assert c.get('/').status_code==200
    b=c.get('/api/bootstrap').json()
    assert b['can_stream']==(origin!=PUBLIC)
    assert b['remote']==(origin!=LOCAL)
    with app.state.security.connect() as sql:
        stored=sql.execute('SELECT * FROM sessions').fetchone()
        raw=r.cookies.get(REMOTE_COOKIE if origin!=LOCAL else LOCAL_COOKIE)
        assert stored['digest']!=raw and len(stored['digest'])==64
    assert PASSWORD not in (tmp_path/'config/owner.json').read_text()


def test_remote_media_only_private_path(tmp_path):
    app=app_env(tmp_path);item=add_sample(app,tmp_path)
    pc=client(app);private=client(app,PRIVATE);public=client(app,PUBLIC)
    for f in item['files']:
        assert private.get('/media/'+f['id']).status_code==200
        assert pc.get('/media/'+f['id']).status_code==200
        restricted=f['kind'] in {'video','audio','preview'}
        assert public.get('/media/'+f['id']).status_code==(403 if restricted else 200)
        assert public.get('/media/'+f['id']+'?download=1').status_code==(403 if restricted else 200)
        if not restricted:assert public.get('/api/text/'+f['id']).status_code==200
    vid=next(f for f in item['files'] if f['kind']=='video')
    r=private.get('/media/'+vid['id'],headers={'Range':'bytes=2-8'})
    assert r.status_code==206 and r.content==b'2345678'
    assert 'no-store' in r.headers['cache-control']


def test_host_and_direct_peer_boundaries(tmp_path):
    app=app_env(tmp_path)
    for origin in ('https://evil.example','http://192.168.1.15:8765'):
        assert client(app,origin,False).get('/login').status_code==403
    c=TestClient(app,base_url=PRIVATE,client=('192.168.1.4',53333))
    assert c.get('/login').status_code==403
    c=client(app,LOCAL,False)
    for k in ['x-forwarded-for','x-forwarded-proto','cf-connecting-ip','forwarded','tailscale-user-login']:
        assert c.get('/login',headers={k:'anything'}).status_code==403
    assert client(app,PRIVATE,False).get('/login',headers={'x-forwarded-proto':'http'}).status_code==403


def test_csrf_and_foreign_origin(tmp_path):
    app=app_env(tmp_path);c=client(app,PRIVATE)
    assert c.post('/api/tags',json={'name':'태그'},headers={'X-YME-Token':'wrong'}).status_code==403
    assert c.post('/api/tags',json={'name':'태그'},headers={'Origin':'https://evil.test'}).status_code==403
    del c.headers['Origin']
    assert c.post('/api/tags',json={'name':'태그'}).status_code==403
    assert c.post('/auth/login',json={'username':USER,'password':PASSWORD}).status_code==403


def test_logout_and_logout_all(tmp_path):
    app=app_env(tmp_path);a=client(app);b=client(app,PRIVATE)
    assert a.post('/api/auth/logout',json={}).status_code==200
    assert a.get('/api/items').status_code==401 and b.get('/api/items').status_code==200
    a=client(app)
    assert b.post('/api/auth/logout-all',json={}).status_code==200
    assert a.get('/api/items').status_code==401 and b.get('/api/items').status_code==401


def test_sessions_survive_restart_but_expire(tmp_path):
    app=app_env(tmp_path);c=client(app,PRIVATE)
    cookies=c.cookies
    new=app_env(tmp_path);d=client(new,PRIVATE,False);d.cookies.update(cookies)
    assert d.get('/api/items').status_code==200
    with new.state.security.connect() as sql:sql.execute('UPDATE sessions SET expires=?',(time.time()-1,))
    assert d.get('/api/items').status_code==401


def test_idle_timeout_and_password_reset(tmp_path):
    app=app_env(tmp_path);c=client(app)
    with app.state.security.connect() as sql:sql.execute('UPDATE sessions SET seen=?',(time.time()-4000,))
    assert c.get('/api/items').status_code==401
    c=client(app)
    set_owner(tmp_path/'config',USER,'changed-very-long-password')
    assert c.get('/api/items').status_code==401


def test_global_login_limit_and_generic_error(tmp_path):
    app=app_env(tmp_path);c=client(app,PRIVATE,False)
    for i in range(8):
        r=c.post('/auth/login',json={'username':USER if i%2 else 'not-real','password':'wrong-password-abc'},headers={'x-forwarded-for':f'10.0.0.{i}'})
        assert r.status_code==401 and r.json()['detail']=='아이디 또는 비밀번호가 맞지 않습니다.'
    assert c.post('/auth/login',json={'username':USER,'password':PASSWORD}).status_code==429
    with app.state.security.connect() as sql:sql.execute('DELETE FROM attempts')
    assert c.post('/auth/login',json={'username':USER,'password':PASSWORD}).status_code==200


def test_remote_os_features_blocked(tmp_path):
    app=app_env(tmp_path);c=client(app,PRIVATE);item=add_sample(app,tmp_path)
    for path,body in [('/api/jobs/import',{'path':str(tmp_path)}),('/api/choose-folder',{}),
                       (f"/api/items/{item['id']}/open-folder",{}),(f"/api/files/{item['files'][0]['id']}/open",{})]:
        assert c.post(path,json=body).status_code==403


def test_large_bodies_rejected(tmp_path):
    c=client(app_env(tmp_path))
    assert c.post('/api/tags',content=b'x'*1_000_001,headers={'Content-Type':'application/json'}).status_code==413
    assert c.post('/auth/login',content=b'x'*4097,headers={'Content-Type':'application/json'}).status_code==413
    assert c.post('/auth/login',json={'username':USER,'password':['bad']}).status_code==400


def test_scope_binding(tmp_path):
    app=app_env(tmp_path);c=client(app,PRIVATE)
    stolen=c.cookies.get(REMOTE_COOKIE)
    other=client(app,PUBLIC,False);other.cookies.set(REMOTE_COOKIE,stolen,domain='manage.example.com',path='/')
    assert other.get('/api/items').status_code==401


@pytest.mark.parametrize('bad',['http://a.ts.net','https://127.0.0.1','https://localhost','https://a.ts.net/path',
                                  'https://user:pass@a.ts.net','https://a.ts.net:8443','https://*.ts.net','https://a..ts.net'])
def test_origin_validation(bad):
    with pytest.raises(ValueError):https_origin(bad,True)


def test_migration_tags_files_and_queue_preserved_safely(tmp_path):
    old=tmp_path/'old';store=Store(old/'data');out=old/'output';out.mkdir()
    (out/'20260919_title_transcript.txt').write_text('example')
    store.scan(out,['기존 태그']);item=store.listing()['items'][0];store.set_favorite(item['id'],True)
    from yme.tasks import Manager
    jid=Manager(store).enqueue('extract',{'text':'eu5H0iRJsJ4'})
    target=tmp_path/'new';migrate_library(old,target)
    copied=Store(target)
    assert copied.item(item['id'])['favorite']==1
    assert copied.item(item['id'])['tags'][0]['name']=='기존 태그'
    assert copied.jobs()[0]['status']=='interrupted'
    assert store.jobs()[0]['status']=='queued'
    with pytest.raises(ValueError):migrate_library(old,target)
    assert (out/'20260919_title_transcript.txt').read_text()=='example'


def test_service_mode_disables_desktop_actions(tmp_path,monkeypatch):
    monkeypatch.setenv('YME_SERVICE_MODE','1');c=client(app_env(tmp_path))
    assert c.get('/api/bootstrap').json()['desktop_actions'] is False
    assert c.post('/api/choose-folder',json={}).status_code==400


def test_no_legacy_pin_bypass(tmp_path):
    c=client(app_env(tmp_path),login=False)
    c.cookies.set('yme_lan_session','anything')
    assert c.get('/api/items').status_code==401
    assert c.post('/lan-login',data={'pin':'123456'}).status_code==401


def test_mobile_assets_kept():
    root=Path(__file__).parents[1]
    s=(root/'static/index.html').read_text(encoding='utf-8')
    assert 'mobile-nav' in s and 'mobile-tags-dialog' in s
    assert 'logout-all' in s and 'private-media-link' in s
