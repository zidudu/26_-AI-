"""V5 실행 진입점. 수동·예약 실행이 동일한 코드와 이력을 사용합니다."""
import argparse
from contextlib import ExitStack,redirect_stdout,redirect_stderr
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

from collector import CollectorError,KST,login
from settings import read_settings,DEFAULT_CONFIG,VERSION
from keywords import parse_keywords
from history import History
from run_lock import RunLock
from run_report import RunReport,atomic_json
from sync_runner import execute


def browser_command(cfg,target,command,run,history):
    try:
        from playwright.sync_api import sync_playwright,Error,TimeoutError as PWTimeout
    except ImportError:
        raise CollectorError('NOT_INSTALLED','먼저 01_install_v5.bat를 실행하세요.') from None
    try:
        cfg['profile_dir'].mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            options={'user_data_dir':str(cfg['profile_dir']),'headless':False,'locale':'ko-KR',
                     'timezone_id':'Asia/Seoul','viewport':{'width':1365,'height':900}}
            if cfg['browser']!='chromium': options['channel']=cfg['browser']
            try: context=pw.chromium.launch_persistent_context(**options)
            except Error:
                raise CollectorError('BROWSER_START_FAILED','브라우저를 실행하지 못했습니다. 다른 수집기 창과 설치 상태를 확인하세요.') from None
            try:
                if command=='login': login(context,target,cfg,PWTimeout); return 0
                return execute(run,history,context.new_page(),PWTimeout)
            finally:
                try: context.close()
                except Error: print('[참고] 브라우저가 닫혔는지 확인하세요.')
    except Error:
        raise CollectorError('BROWSER_ERROR','브라우저 연결 또는 페이지 처리에 실패했습니다. 남은 작업은 다음 실행에서 복구합니다.') from None


def do_command(args):
    cfg=None; run=None; history=None; result=1; error=None
    try:
        cfg,target=read_settings(args.config)
        if args.keywords is not None: cfg['keywords']=parse_keywords(args.keywords)
        if args.initial_count is not None:
            if not 1<=args.initial_count<=100: raise CollectorError('INVALID_COUNT','초기 선정 개수는 1~100입니다.')
            cfg['initial_count']=args.initial_count
        if args.prompt:
            value=input(f'검색어 쉼표 구분 (Enter: {", ".join(cfg["keywords"])}, 취소: /q): ').strip()
            if value=='/q': raise CollectorError('CANCELLED','입력을 취소했습니다.')
            if value: cfg['keywords']=parse_keywords(value)
            print('이번 입력은 이번 실행에만 적용됩니다. 예약 검색어는 config_v5.json에서 변경하세요.')
        print(f'[V{VERSION}] {args.command} / 카페 {cfg["cafe_id"]} / {", ".join(cfg["keywords"])}')
        print(f'초기 선정: 키워드별 {cfg["initial_count"]}개 / 이후: 이전 확인 날짜와 {cfg["overlap_days"]}일 겹쳐 검색')
        if args.command=='check':
            print(f'[설정 정상] 이력: {cfg["history_db"]}\n결과: {cfg["output_dir"]}\n예약 기본 시각: {cfg["schedule_time"]} (PC 현지 시각)')
            print('이 검사는 설정 확인입니다. 브라우저 로그인과 예약 등록 여부는 별도 확인하세요.')
            return 0
        with ExitStack() as stack:
            stack.enter_context(RunLock(cfg['profile_lock']))
            stack.enter_context(RunLock(str(cfg['history_db'])+'.lock'))
            if args.command=='login': return browser_command(cfg,target,'login',None,None)
            history=History(cfg); stack.callback(history.close)
            if args.command=='history':
                print(json.dumps({'counts':history.stats(),'searches':{k:history.scan(k) for k in cfg['keywords']}},ensure_ascii=False,indent=2))
                return 0
            run=RunReport(cfg,args.command)
            print(f'실행 폴더: {run.folder}')
            try:
                run.s['import_report']=history.import_results(cfg['import_dirs']+[cfg['output_dir']])
                run.s['recovery']=history.recover_and_check()
                report=run.s['import_report']
                print(f'[기존 결과 등록] 파일 {report["files_registered"]} / 변경 없음 {report["unchanged_files"]} / 등급 등 보류 {report["held_registered"]} / 검증 제외 {report["invalid_files"]}')
                if report['invalid_files']: run.s['warnings'].append('INVALID_IMPORT_FILES')
                run.checkpoint()
                if args.command=='import': result=run.finish(history)
                else:
                    if args.command in ('recheck','retry'):
                        count=history.requeue(cfg['keywords'],'held' if args.command=='recheck' else 'errors')
                        run.s['manual_requeued']=count
                        print(f'[재확인 등록] {count}개')
                    # 검색하지 않는 복구 실행에서 대상이 없으면 브라우저를 열지 않습니다.
                    active=history.active_rows(cfg['keywords'])
                    due=any(r['status'] in ('pending','collecting','retry') for r in active)
                    if args.command!='sync' and not due: result=run.finish(history)
                    else: result=browser_command(cfg,target,args.command,run,history)
            except (KeyboardInterrupt,EOFError): error=('CANCELLED','사용자 입력 또는 실행이 중단됐습니다.')
            except CollectorError as exc: error=(exc.code,str(exc))
            except sqlite3.Error: error=('HISTORY_ERROR','이력을 읽거나 저장하지 못했습니다. 원본 결과 파일을 보존하고 DB 상태를 확인하세요.')
            except OSError: error=('FILE_ERROR','파일을 읽거나 쓰지 못했습니다. 디스크와 폴더 권한을 확인하세요.')
            except Exception: error=('INTERNAL_ERROR','예상하지 못한 오류입니다. 실행 요약과 오류 코드를 확인하세요.')
            if error:
                print(f'[중단: {error[0]}] {error[1]}',file=sys.stderr)
                try: result=run.finish(history,*error)
                except (OSError,sqlite3.Error):
                    print('[요약 저장 실패] 이미 저장한 글과 DB를 보존했습니다. 다음 실행 시 복구를 시도합니다.',file=sys.stderr)
                    result=130 if error[0]=='CANCELLED' else 1
            return result
    except (KeyboardInterrupt,EOFError): error=('CANCELLED','입력을 취소했습니다.'); result=130
    except CollectorError as exc: error=(exc.code,str(exc)); result=4 if exc.code=='ALREADY_RUNNING' else (130 if exc.code=='CANCELLED' else 1)
    except sqlite3.Error: error=('HISTORY_ERROR','이력 데이터베이스를 열지 못했습니다.')
    except OSError: error=('FILE_ERROR','파일 또는 폴더를 사용할 수 없습니다.')
    except Exception: error=('INTERNAL_ERROR','예상하지 못한 초기화 오류입니다.')
    finally:
        if cfg and args.command not in ('check','history','login'):
            try:
                cfg['output_dir'].mkdir(parents=True,exist_ok=True)
                atomic_json(cfg['output_dir']/'latest_status.json',{'time':datetime.now(KST).isoformat(),
                    'command':args.command,'code':error[0] if error else (run.s['code'] if run else 'UNKNOWN'),
                    'summary_file':str(run.folder/'summary.json') if run else None})
            except OSError: pass
    if error: print(f'[중단: {error[0]}] {error[1]}',file=sys.stderr)
    return result


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8',errors='replace')
    p=argparse.ArgumentParser(description='V5: 이력 기반 신규 글 수집과 복구')
    p.add_argument('command',nargs='?',default='sync',choices=['sync','resume','recheck','retry','import','history','check','login'])
    p.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    p.add_argument('--keywords'); p.add_argument('--initial-count',type=int)
    p.add_argument('--prompt',action='store_true'); p.add_argument('--scheduled',action='store_true')
    args=p.parse_args(argv)
    if args.scheduled and (args.prompt or args.command!='sync'): p.error('예약 실행은 입력 없는 sync만 지원합니다.')
    if args.prompt and args.command!='sync': p.error('--prompt는 sync에서만 사용하세요.')
    if args.scheduled:
        # 설정 오류도 로그에 남도록 설정을 읽기 전, 실행 파일 기준 경로에 로그를 만듭니다.
        folder=DEFAULT_CONFIG.parent/'output_v5/scheduler_logs'; folder.mkdir(parents=True,exist_ok=True)
        path=folder/(datetime.now(KST).strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8]+'.log')
        with path.open('w',encoding='utf-8') as f,redirect_stdout(f),redirect_stderr(f):
            return do_command(args)
    return do_command(args)

if __name__=='__main__': raise SystemExit(main())
