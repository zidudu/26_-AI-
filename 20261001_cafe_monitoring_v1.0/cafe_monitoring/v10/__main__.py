"""Start the local V10 service, or perform an explicit CLI migration."""
import argparse
from pathlib import Path
import threading
import webbrowser
from .common import ROOT,InstanceLock,Problem
from .service import Service
from .server import AppServer


def main():
    parser=argparse.ArgumentParser(description='자동차 동호회 모니터링 V10')
    parser.add_argument('--data',default=str(ROOT/'data_v10'))
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--legacy-root',help='기존 naver_cafe 폴더 (설정/로그인 프로필 읽기)')
    parser.add_argument('--import-legacy',help='기존 naver_cafe 폴더의 V9 실행 자료를 DB로 이관')
    args=parser.parse_args()
    with InstanceLock(Path(args.data)/'server.lock'):
        service=Service(args.data)
        # A previous server may have closed while its isolated worker finishes.
        # Never relabel or launch another worker while its OS lock is held.
        with InstanceLock(Path(args.data)/'worker.lock'):service.db.recover()
        if args.legacy_root:
            p=Path(args.legacy_root).expanduser().resolve()
            if not (p/'config_v5.json').is_file():raise Problem('config_v5.json이 있는 기존 프로그램 폴더를 지정하세요.')
            service.db.put('legacy_root',str(p))
        if args.import_legacy:
            from .migrate import import_project
            result=import_project(service.db,args.import_legacy)
            from .common import dumps
            print(dumps(result));return
        server=AppServer(('127.0.0.1',args.port),service)
        thread=threading.Thread(target=service.loop,daemon=True);thread.start()
        url=f'http://127.0.0.1:{args.port}'
        print('자동차 동호회 모니터링 V10:',url,flush=True)
        print('예약 실행은 이 창이 열려 있고 PC가 깨어 있는 동안 동작합니다.',flush=True)
        if not args.no_browser:webbrowser.open(url)
        try:server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            active=service.db.active()
            if active:service.db.stop(active);print('진행 중인 작업에 중지를 요청했습니다. 작업이 끝난 뒤 다시 시작하세요.')
        finally:service.shutdown.set();service.codex.close();server.server_close()


if __name__=='__main__':
    try:main()
    except (Problem,OSError) as exc:print('실행할 수 없습니다:',exc);raise SystemExit(1)
