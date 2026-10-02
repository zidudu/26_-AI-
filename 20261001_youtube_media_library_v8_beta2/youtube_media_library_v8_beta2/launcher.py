"""고정 포트, loopback 바인딩, 종료 요청/회전 로그를 갖춘 서버 실행기."""
from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from yme.remote_security import ROOT, ServerConfig, atomic_private_json


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self,path):
        self.f=open(path,'a+b')
        if self.f.seek(0,2)==0:self.f.write(b'0');self.f.flush()
        self.f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.f.close();raise AlreadyRunningError('같은 라이브러리의 beta 서버가 이미 실행 중입니다.') from None
    def close(self):self.f.close()


class LogStream:
    def __init__(self,logger,level):self.logger,self.level=logger,level
    def write(self,value):
        for line in value.rstrip().splitlines():
            if line:self.logger.log(self.level,line)
        return len(value)
    def flush(self):pass
    def isatty(self):return False


def configure_logs():
    (ROOT/'logs').mkdir(exist_ok=True)
    handler=RotatingFileHandler(ROOT/'logs/server.log',maxBytes=5_000_000,backupCount=5,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log=logging.getLogger('yme.server');log.setLevel(logging.INFO);log.addHandler(handler);log.propagate=False
    sys.stdout=LogStream(log,logging.INFO);sys.stderr=LogStream(log,logging.ERROR)
    for name in ('uvicorn','uvicorn.error'):
        target=logging.getLogger(name);target.handlers=[handler];target.setLevel(logging.INFO);target.propagate=False


def running_server_ready(config_dir, port, timeout=5):
    """같은 라이브러리의 잠금이 잡혀 있을 때 실행 정보와 응답을 확인합니다."""
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline=time.monotonic()+timeout
    while True:
        try:
            runtime=json.loads((config_dir/'runtime.json').read_text(encoding='utf-8-sig'))
            if (runtime.get('port')==port and isinstance(runtime.get('pid'),int)
                    and runtime['pid']>0 and runtime.get('id')):
                with opener.open(f'http://127.0.0.1:{port}/api/health',timeout=.5) as response:
                    health=json.load(response)
                if (health.get('application')=='youtube-media-library-beta'
                        and health.get('status')=='ok'):
                    return True
        except (OSError,ValueError,TypeError,AttributeError):
            pass
        if time.monotonic()>=deadline:
            return False
        time.sleep(.1)


def main():
    p=argparse.ArgumentParser(description='YouTube Media Library V8 beta.2')
    p.add_argument('--no-browser',action='store_true');p.add_argument('--service',action='store_true')
    p.add_argument('--config-dir',default=str(ROOT/'config'))
    args=p.parse_args();config_dir=Path(args.config_dir).resolve();cfg=ServerConfig.load(config_dir)
    if args.service:
        os.environ['YME_SERVICE_MODE']='1';configure_logs()
    else:
        for stream in (sys.stdout,sys.stderr):
            if hasattr(stream,'reconfigure'):stream.reconfigure(errors='replace')
    data=Path(cfg.data_dir);data.mkdir(parents=True,exist_ok=True)
    try:
        lock=InstanceLock(data/'.v8-beta.lock')
    except AlreadyRunningError:
        if not running_server_ready(config_dir,cfg.port):
            raise RuntimeError('서버가 이미 실행 중이지만 응답을 확인하지 못했습니다. 04_status.bat과 logs/server.log를 확인해 주세요.') from None
        url=f'http://127.0.0.1:{cfg.port}'
        print('[OK] 이미 실행 중인 서버를 사용합니다:',url,flush=True)
        if not args.no_browser and not args.service:
            webbrowser.open(url)
        return 0
    try:
        with socket.socket() as sock:
            # 공유 포트를 임의로 변경하지 않습니다. 외부 프록시와 같은 포트를 사용해야 합니다.
            sock.bind(('127.0.0.1',cfg.port))
    except OSError:
        lock.close();raise RuntimeError(f'{cfg.port} 포트가 사용 중입니다. 이전 V8/alpha 서버를 종료하세요.') from None
    from app import create_app
    import uvicorn
    app=create_app(config=cfg,security_dir=config_dir)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=cfg.port,workers=1,
        proxy_headers=False,access_log=False,log_level='warning',log_config=None,
        limit_concurrency=32,timeout_keep_alive=5,timeout_graceful_shutdown=30))
    run_id=secrets.token_hex(24);runtime=config_dir/'runtime.json';stopfile=config_dir/'stop.request'
    atomic_private_json(runtime,{'id':run_id,'pid':os.getpid(),'port':cfg.port,'started_at':time.time()})
    stopfile.unlink(missing_ok=True)
    done=threading.Event()
    def watch_stop():
        while not done.wait(.5):
            try:
                if json.loads(stopfile.read_text()).get('id')==run_id:
                    print('[STOP] 서버를 정상 종료합니다.',flush=True);server.should_exit=True;return
            except (OSError,ValueError):pass
    threading.Thread(target=watch_stop,daemon=True).start()
    url=f'http://127.0.0.1:{cfg.port}'
    print('YouTube Media Library V8 beta.2',flush=True)
    print('PC:',url,flush=True);print('Private:',cfg.private_url or '(02_connect_tailscale.bat에서 설정)',flush=True)
    if cfg.fast_url:print('Private fast path:',cfg.fast_url,flush=True)
    if cfg.cloudflare_url:print('Cloudflare management only:',cfg.cloudflare_url,flush=True)
    print('로그인은 PC/휴대폰 모두 필요합니다. 종료: Ctrl+C 또는 stop_v8_beta.bat',flush=True)
    if not args.no_browser and not args.service:
        def open_ready():
            for _ in range(100):
                if done.wait(.2):return
                try:
                    with urllib.request.urlopen(url+'/api/health',timeout=.4):pass
                    webbrowser.open(url);return
                except Exception:pass
        threading.Thread(target=open_ready,daemon=True).start()
    try:server.run()
    finally:
        done.set();lock.close()
        runtime.unlink(missing_ok=True);stopfile.unlink(missing_ok=True)
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as e:
        print('[ERROR]',e,flush=True);raise SystemExit(1)
