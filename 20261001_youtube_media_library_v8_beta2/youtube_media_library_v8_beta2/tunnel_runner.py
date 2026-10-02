"""선택 사항: 관리 전용 cloudflared 실행/종료와 토큰 비노출 로그.

영상 전달은 app.py에서 차단됩니다. 공개 tunnel URL은 사용자 Cloudflare 계정에서
설정해야 합니다. 현재 프로그램이 tunnel 자체를 생성하거나 계정에 로그인하지 않습니다.
"""
from __future__ import annotations
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time
from connect_remote import find_cloudflared
from launcher import InstanceLock
from yme.remote_security import ROOT,ServerConfig,atomic_private_json


from yme.windows_job import windows_job


def main():
    p=argparse.ArgumentParser();p.add_argument('--service',action='store_true');p.add_argument('--no-browser',action='store_true');p.add_argument('--stop',action='store_true');args=p.parse_args()
    config=ROOT/'config';runtime=config/'tunnel-runtime.json';stopfile=config/'tunnel-stop.request'
    if args.stop:
        if not runtime.exists():print('실행 중인 tunnel 기록이 없습니다.');return 0
        value=json.loads(runtime.read_text())
        atomic_private_json(stopfile,{'id':value['id']})
        for _ in range(80):
            if not runtime.exists():print('Tunnel을 종료했습니다.');return 0
            time.sleep(.25)
        print('종료 응답이 없습니다. 로그와 작업 관리자를 확인하세요.');return 1
    cfg=ServerConfig.load(config)
    token_file=config/'tunnel.token'
    if not cfg.cloudflare_url or not token_file.is_file():raise RuntimeError('OPTIONAL_setup_cloudflare.bat을 먼저 실행하세요.')
    exe=find_cloudflared()
    if not exe:raise RuntimeError('cloudflared가 설치되어 있지 않습니다.')
    version=subprocess.run([exe,'--version'],capture_output=True,timeout=10).stdout.decode('utf-8','replace')
    match=re.search(r'(\d{4})\.(\d+)\.(\d+)',version)
    if not match or tuple(map(int,match.groups()))<(2025,4,0):
        raise RuntimeError('token-file을 지원하는 cloudflared 2025.4.0 이상이 필요합니다.')
    lock=InstanceLock(config/'.cloudflare.lock')
    (ROOT/'logs').mkdir(exist_ok=True)
    logger=logging.getLogger('yme.tunnel');logger.setLevel(logging.INFO)
    handler=RotatingFileHandler(ROOT/'logs/tunnel.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler)
    stop_event=threading.Event();run_id=secrets.token_hex(24);close_job=None;proc=None
    # 설치 토큰을 명령줄/환경변수로 넣지 않고 제한된 파일의 경로만 넘깁니다.
    cmd=[exe,'tunnel','--no-autoupdate','run','--token-file',str(token_file)]
    try:
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
             start_new_session=os.name!='nt')
        close_job=windows_job(proc)
        atomic_private_json(runtime,{'id':run_id,'pid':os.getpid(),'child':proc.pid,'started_at':time.time()})
        stopfile.unlink(missing_ok=True)
        def read_logs():
            for line in iter(proc.stdout.readline,b''):
                text=line.decode('utf-8','replace').strip()
                # 방어적으로 bearer/token처럼 보이는 문자열은 치환합니다.
                text=re.sub(r'(?i)(token[=: ]+)[A-Za-z0-9_+/.=-]{20,}',r'\1[redacted]',text)
                logger.info(text)
        t=threading.Thread(target=read_logs,daemon=True);t.start()
        def request_stop(*_):stop_event.set()
        signal.signal(signal.SIGINT,request_stop);signal.signal(signal.SIGTERM,request_stop)
        if not args.service:print('Cloudflare 관리 터널 실행 중. 종료: Ctrl+C. 상태: logs/tunnel.log')
        while proc.poll() is None and not stop_event.wait(.4):
            try:
                if json.loads(stopfile.read_text()).get('id')==run_id:stop_event.set()
            except (OSError,ValueError):pass
        stopped=stop_event.is_set()
        if proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        t.join(timeout=3)
        return 0 if stopped else (proc.returncode or 1)
    finally:
        if proc and proc.poll() is None:proc.terminate()
        if close_job:close_job()
        runtime.unlink(missing_ok=True);stopfile.unlink(missing_ok=True);lock.close()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print('[Tunnel 오류]',exc);raise SystemExit(1)
