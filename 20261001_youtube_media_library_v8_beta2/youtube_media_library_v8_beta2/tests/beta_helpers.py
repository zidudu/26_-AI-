from pathlib import Path
from fastapi.testclient import TestClient
from app import create_app
from yme.remote_security import ServerConfig,set_owner
USER='test-owner'
PASSWORD='This-is-a-test-password-123!'
LOCAL='http://127.0.0.1:8765'
PRIVATE='https://test-pc.example-tail.ts.net'
PUBLIC='https://manage.example.com'


def app_env(path: Path, **kw):
    cfg=ServerConfig(private_url=PRIVATE,cloudflare_url=PUBLIC,data_dir=str(path/'data'),output_dir=str(path/'output'))
    folder=path/'config'
    if not (folder/'owner.json').exists():set_owner(folder,USER,PASSWORD)
    return create_app(config=cfg,security_dir=folder,run_jobs=False,**kw)


def client(app, origin=LOCAL, login=True):
    c=TestClient(app,base_url=origin,client=('127.0.0.1',53333))
    c.headers['Origin']=origin
    if login:
        r=c.post('/auth/login',json={'username':USER,'password':PASSWORD})
        assert r.status_code==200,r.text
        c.headers['X-YME-Token']=c.get('/api/bootstrap').json()['token']
    return c
