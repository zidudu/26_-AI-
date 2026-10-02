"""실제 subprocess 서버 기동·로그인·로컬 종료 요청 검사. 외부 provider는 사용하지 않음."""
from pathlib import Path
import json
import socket
import subprocess
import sys
import time
import httpx
import pytest
import launcher
from yme.remote_security import ServerConfig,set_owner,atomic_private_json


@pytest.mark.parametrize('service',[False,True])
def test_real_server_lifecycle(tmp_path,service,monkeypatch):
    root=Path(__file__).parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config=tmp_path/'config'
    ServerConfig(port=port,data_dir=str(tmp_path/'data'),output_dir=str(tmp_path/'output')).save(config)
    set_owner(config,'launcher-test','Actual-launcher-test-pass')
    command=[sys.executable,str(root/'launcher.py'),'--config-dir',str(config),'--no-browser']
    if service:command.append('--service')
    log=(tmp_path/'startup.log').open('wb')
    proc=subprocess.Popen(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    try:
        origin=f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=origin,headers={'Origin':origin},trust_env=False,timeout=2) as c:
            for _ in range(100):
                try:
                    r=c.get('/api/health')
                    if r.status_code==200:break
                except httpx.HTTPError:pass
                assert proc.poll() is None,(tmp_path/'startup.log').read_text(errors='replace')
                time.sleep(.1)
            assert r.status_code==200
            assert c.get('/api/items').status_code==401
            assert c.post('/auth/login',json={'username':'launcher-test','password':'Actual-launcher-test-pass'}).status_code==200
            b=c.get('/api/bootstrap').json()
            assert b['desktop_actions']==(not service)
        run=json.loads((config/'runtime.json').read_text())
        # 자동 시작 서버가 켜진 뒤 BAT를 다시 실행해도 같은 서버를 재사용합니다.
        duplicate=subprocess.run(command,cwd=root,capture_output=True,timeout=12)
        assert duplicate.returncode==0,duplicate.stdout+duplicate.stderr
        assert proc.poll() is None
        assert json.loads((config/'runtime.json').read_text())==run

        # 실제 기본 실행 경로는 기존 서버의 브라우저를 열고 성공으로 종료합니다.
        opened=[]
        monkeypatch.setattr(launcher.webbrowser,'open',lambda url: opened.append(url))
        monkeypatch.setattr(sys,'argv',['launcher.py','--config-dir',str(config)])
        assert launcher.main()==0
        assert opened==[origin]
        assert json.loads((config/'runtime.json').read_text())==run
        atomic_private_json(config/'stop.request',{'id':run['id']})
        assert proc.wait(timeout=15)==0
        assert not (config/'runtime.json').exists()
    finally:
        if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
        log.close()


def test_unrelated_port_is_not_reused(tmp_path,monkeypatch):
    config=tmp_path/'config'
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        port=listener.getsockname()[1]
        listener.listen()
        ServerConfig(port=port,data_dir=str(tmp_path/'data'),output_dir=str(tmp_path/'output')).save(config)
        opened=[]
        monkeypatch.setattr(launcher.webbrowser,'open',lambda url: opened.append(url))
        monkeypatch.setattr(sys,'argv',['launcher.py','--config-dir',str(config)])
        with pytest.raises(RuntimeError,match=str(port)):
            launcher.main()
        assert not opened
        assert not (config/'runtime.json').exists()


def test_unhealthy_locked_server_is_not_reused(tmp_path,monkeypatch):
    config=tmp_path/'config'
    data=tmp_path/'data'
    data.mkdir()
    ServerConfig(data_dir=str(data),output_dir=str(tmp_path/'output')).save(config)
    with_lock=launcher.InstanceLock(data/'.v8-beta.lock')
    opened=[]
    check=launcher.running_server_ready
    monkeypatch.setattr(launcher,'running_server_ready',lambda folder,port: check(folder,port,timeout=0))
    monkeypatch.setattr(launcher.webbrowser,'open',lambda url: opened.append(url))
    monkeypatch.setattr(sys,'argv',['launcher.py','--config-dir',str(config)])
    try:
        with pytest.raises(RuntimeError,match='04_status'):
            launcher.main()
        assert not opened
    finally:
        with_lock.close()
