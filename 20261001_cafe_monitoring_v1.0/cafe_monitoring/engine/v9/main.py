"""Windows entry point. Scheduled `run` never prompts for input."""
from contextlib import ExitStack,redirect_stdout,redirect_stderr
from copy import deepcopy
from datetime import datetime,timedelta
import argparse
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from v8.configuration import KST,V8Error,read_json,write_json,parse_time,addresses
from v8.main import ask,yesno
from v8.period_run import prompt_test_mail
from v8.pipeline import Tee,stamp
from v9.configuration import load,validate,from_v8,selected,performance
from v9.pipeline import Runner,PATTERN
from v9 import __version__


def choose_cafes(cfg,default='all'):
    for index,c in enumerate(cfg['cafes'],1):
        print(f"{index}. {c['code']} · {c['name']} / {c['slug']} / ID {c['club_id'] or '자동 확인 예정'} / {'사용' if c['enabled'] else '꺼짐'}")
    answer=ask('카페 선택: all 또는 번호를 쉼표로 구분',default)
    if answer.lower()=='all': return [c['slug'] for c in cfg['cafes']]
    try:
        ids=[int(v.strip()) for v in answer.split(',')]
        if not ids or any(not 1<=n<=len(cfg['cafes']) for n in ids): raise ValueError()
    except ValueError as exc:
        raise V8Error('INVALID_CAFE_SELECTION','예: all 또는 1,2,7') from exc
    return [c['slug'] for n,c in enumerate(cfg['cafes'],1) if n in ids]


def save_config(cfg):
    cfg=validate(cfg)
    path=ROOT/'config_v9.json'
    if path.exists():
        (ROOT/('config_v9_before_'+datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')+'.json')).write_bytes(path.read_bytes())
    write_json(path,cfg)


def choose_concurrency(default):
    value=ask('동시 수집 카페 수 1·2·3 / 1: 기존 순차, 2·3: 병렬',str(default))
    if value not in ('1','2','3'):
        raise V8Error('INVALID_CONCURRENCY','동시 수집 수는 1, 2, 3 중 하나입니다.')
    return int(value)


def collection_setup():
    cfg=load(ROOT)
    cfg['collection']['concurrency']=choose_concurrency(cfg['collection']['concurrency'])
    save_config(cfg)
    print('[수집 방식 저장]',cfg['collection']['concurrency'],'개 카페 동시 수집')
    print('다음 실행부터 적용됩니다. 예약 재등록은 필요 없습니다.')


def choose_recheck(default):
    print('빠른 방식: 한 페이지에서 판정이 끝나면 최초 조회 목록을 사용합니다. 여러 페이지는 재확인합니다.')
    answer=ask('검색 목록 확인 1: 빠른 방식, 2: 기존 전체 재확인', '1' if default=='adaptive' else '2')
    if answer not in ('1','2'):
        raise V8Error('INVALID_SEARCH_RECHECK','1 또는 2를 입력하세요.')
    return 'adaptive' if answer=='1' else 'full'


def choose_analysis_concurrency(default):
    answer=ask('AI 동시 분석 수 1·2·3',str(default))
    if answer not in ('1','2','3'):
        raise V8Error('INVALID_ANALYSIS_CONCURRENCY','1, 2, 3 중 하나를 입력하세요.')
    return int(answer)


def show_performance(cfg):
    perf=performance(cfg)
    print('[검색 목록 확인]', '빠른 방식 / 단일 페이지 최초 조회·여러 페이지 재확인' if perf['search_recheck']=='adaptive'
          else '기존 전체 재확인')
    print(f"[AI 동시 분석] {perf['analysis_concurrency']}개")
    print('[PPT 기본 양식 재사용]', '사용 / 최초 실행에 생성' if perf['ppt_template_cache'] else '사용 안 함')
    ppi=perf['ppt_image_ppi']
    print('[PPT 이미지 설정]', f'PNG {ppi}ppi / 최종 표시 inch 기준 / 원본 별도 보존' if ppi else '원본 PNG 사용')


def ppt_image_setup():
    from v9.ppt_images import valid_ppi
    cfg=load(ROOT)
    print('목표 PNG 너비 = 최종 표시 너비(inch) × PPI. 원본보다 확대하지 않습니다.')
    print('기본 125ppi / 예: 110, 120, 125, 130, 140, 150 / 0: 원본 사용. 새 수집부터 적용됩니다.')
    answer=ask('PNG 목표 PPI 96~300 또는 0',str(cfg['performance']['ppt_image_ppi']))
    if not answer.isdigit() or not valid_ppi(int(answer)):
        raise V8Error('INVALID_PPT_IMAGE_PPI','0 또는 96~300 정수를 입력하세요.')
    cfg['performance']['ppt_image_ppi']=int(answer)
    save_config(cfg)
    show_performance(cfg)
    print('[PPT 이미지 설정 저장] 기존 캡처·PPT는 변경하지 않습니다. 예약 재등록은 필요 없습니다.')


def performance_setup():
    cfg=load(ROOT)
    cfg['collection']['concurrency']=choose_concurrency(cfg['collection']['concurrency'])
    cfg['performance']['search_recheck']=choose_recheck(cfg['performance']['search_recheck'])
    cfg['performance']['analysis_concurrency']=choose_analysis_concurrency(cfg['performance']['analysis_concurrency'])
    cfg['performance']['ppt_template_cache']=yesno('PPT 기본 양식을 저장하여 다음 실행에 재사용',cfg['performance']['ppt_template_cache'])
    save_config(cfg)
    show_performance(cfg)
    print('[성능 설정 저장] 다음 실행부터 적용됩니다. 예약 재등록은 필요 없습니다.')


def setup():
    path=ROOT/'config_v9.json'
    cfg=load(ROOT) if path.exists() else from_v8(ROOT)
    print('V8의 예약 시각·수신자를 V9 설정에 복사합니다. V8 설정 파일은 보존합니다.')
    enabled=[str(i) for i,c in enumerate(cfg['cafes'],1) if c['enabled']]
    slugs=choose_cafes(cfg,'all' if len(enabled)==9 else ','.join(enabled))
    for c in cfg['cafes']: c['enabled']=c['slug'] in slugs
    cfg['collection']['concurrency']=choose_concurrency(cfg['collection']['concurrency'])
    cfg['schedule']['time']=ask('예약 시각 HH:MM / 한국시간',cfg['schedule']['time'])
    cfg['schedule']['weekdays_only']=yesno('평일만 실행',cfg['schedule']['weekdays_only'])
    print('처리 이력이 없는 카페에 적용하는 시작 시각입니다. 기존 완료 시각이 있으면 그 시각부터 이어집니다.')
    cfg['schedule']['initial_start']=ask('새 카페 최초 수집 시작 YYYY-MM-DD HH:MM',cfg['schedule']['initial_start'] or
                                       (datetime.now(KST)-timedelta(days=1)).strftime('%Y-%m-%d %H:%M'))
    if parse_time(cfg['schedule']['initial_start'])>datetime.now(KST):
        raise V8Error('FUTURE_START','최초 시작은 현재 시각 이후일 수 없습니다.')
    cfg['mail']['to']=addresses(ask('받는 사람 / 여러 명은 세미콜론',';'.join(cfg['mail']['to'])))
    cc=ask('참조 /none: 비우기',';'.join(cfg['mail']['cc']))
    cfg['mail']['cc']=[] if cc.lower()=='/none' else addresses(cc)
    cfg['mail']['send_partial']=yesno('일부 카페 실패·검토 필요 글이 있어도 검증된 통합 PPT 발송',cfg['mail']['send_partial'])
    if not cfg['mail']['to']: raise V8Error('RECIPIENT_REQUIRED','수신자가 필요합니다.')
    save_config(cfg)
    print('[V9 설정 저장]',path)
    print('[다음] 31_connect_cafes_v9.bat → 32_check_v9.bat → 36_run_period_v9.bat')
    print('자동 예약 전환: 34_register_schedule_v9.bat. 기존 예약을 같은 이름으로 V9에 연결합니다.')


def connect(cfg,backend):
    report={}
    cache_path=ROOT/'output_v9/connections.json'
    cache=read_json(cache_path) if cache_path.exists() else {}
    with backend.browser():
        print('기존 Chrome 로그인 프로필을 사용합니다. 로그인·가입 권한은 카페 화면에서 확인됩니다.')
        for cafe in selected(cfg):
            try:
                cid,evidence=backend.identify(cafe)
                prior=cache.get(cafe['slug'])
                if prior and prior['club_id']!=cid:
                    raise V8Error('CAFE_ID_MISMATCH','저장된 연결 ID와 다릅니다. 임의로 덮어쓰지 않았습니다.')
                if any(x.get('club_id')==cid and slug!=cafe['slug'] for slug,x in cache.items()):
                    raise V8Error('DUPLICATE_CAFE_ID','다른 카페와 clubId가 중복됩니다.')
                cache[cafe['slug']]=dict(club_id=cid,checked_at=stamp(),evidence=evidence)
                write_json(cache_path,cache)
                report[cafe['slug']]=dict(status='connected',club_id=cid,evidence=evidence)
                print(f"[카페 ID 확인] {cafe['name']} / {cafe['slug']} / {cid}")
            except (KeyboardInterrupt,EOFError): raise
            except Exception as exc:
                report[cafe['slug']]=dict(status='failed',code=getattr(exc,'code',type(exc).__name__),message=str(exc))
                report[cafe['slug']]['identity_snapshot']=getattr(exc,'identity_snapshot',None)
                report[cafe['slug']]['traceback']=traceback.format_exc()
                print(f"[연결 확인 실패] {cafe['name']} / {report[cafe['slug']]['code']} / {exc}")
                if getattr(exc,'code','')=='REQUEST_BLOCKED':
                    for rest in selected(cfg):
                        if rest['slug'] not in report:
                            report[rest['slug']]=dict(status='failed',code='DEFERRED_REQUEST_BLOCKED',message='접근 제한으로 연결 확인을 중단했습니다.')
                    break
    write_json(ROOT/'output_v9/connection_check.json',report)
    connected=sum(r['status']=='connected' for r in report.values())
    print(f'[카페 연결 결과] 확인 성공 {connected}개 / 실패 {len(report)-connected}개')
    print('[연결 기록]',ROOT/'output_v9/connection_check.json')
    if connected:
        print('연결된 카페의 실제 게시글 열람 권한·수집은 기간 지정 실행에서 확인합니다.')
    else:
        print('카페 연결을 확인하지 못했습니다. 위 오류와 연결 기록을 확인하세요.')
    return 0 if connected == len(report) and connected else 2


def choose_run():
    rows=[]
    for mode in ('runs','period'):
        for path in (ROOT/'output_v9'/mode).glob('*/run.json'):
            if PATTERN.fullmatch(path.parent.name):
                row=read_json(path)
                rows.append(row)
    rows.sort(key=lambda r:r.get('updated_at',''),reverse=True)
    if not rows: raise V8Error('NO_RUNS','V9 실행 기록이 없습니다.')
    for i,r in enumerate(rows[:30],1):
        print(f"{i}. {r['id']} / {r['status']} / 메일 {r['mail']['status']}")
    answer=ask('실행 번호 또는 목록 번호','1')
    return rows[int(answer)-1]['id'] if answer.isdigit() and 1<=int(answer)<=min(30,len(rows)) else answer


def status():
    path=ROOT/'output_v9/state.json'
    if path.exists():
        state=read_json(path)
        print('[진행 중 예약]',state.get('active') or '없음')
        for slug,row in state['cafes'].items():
            print(f"{slug} / 처리 완료 {row.get('cursor') or '없음'} / 최근 실패 경계 {row.get('failed_end') or '없음'}")
    rows=sorted((ROOT/'output_v9').glob('*/batch_*/run.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    rows+=sorted((ROOT/'output_v9/period').glob('*/run.json'),key=lambda p:p.stat().st_mtime,reverse=True)
    for path in sorted(rows,key=lambda p:p.stat().st_mtime,reverse=True)[:10]:
        row=read_json(path)
        print(row['id'],'/',row['status'],'/ 메일',row['mail']['status'])
    if not rows: print('[V9] 아직 실행 기록이 없습니다.')


def task_command(action):
    cfg=load(ROOT)
    if not cfg['schedule']['initial_start'] or not cfg['mail']['to']:
        raise V8Error('SETUP_REQUIRED','V9 시작 시각·수신자 설정을 확인하세요.')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                           str(ROOT/'v9/schedule.ps1'),'-ProjectRoot',str(ROOT),'-Action',action])
    return result.returncode


def run_command(args,notice):
    if os.name!='nt': raise V8Error('WINDOWS_REQUIRED','Windows 회사 PC에서 실행하세요.')
    if args.command=='status': status(); return 0
    if args.command=='show-result':
        paths=sorted((ROOT/'output_v9/notices').glob('*.txt'))
        if not paths: raise V8Error('NO_RESULTS','예약 실행 결과 알림 기록이 없습니다. 35번에서 실행 기록을 확인하세요.')
        os.startfile(str(paths[-1]));return 0
    from v754.collect.run_lock import RunLock
    from v9.backend import Backend
    with ExitStack() as locks:
        locks.enter_context(RunLock(ROOT/'output_v8/v8.lock'))
        if args.command=='setup': setup();return 0
        if args.command=='collection-setup': collection_setup();return 0
        if args.command=='performance-setup': performance_setup();return 0
        if args.command=='ppt-image-setup': ppt_image_setup();return 0
        if args.command=='ai-setup':
            from v9.ai_setup import setup as ai_setup
            ai_setup(ROOT)
            return 0
        cfg=load(ROOT)
        if args.concurrency is not None:
            cfg['collection']['concurrency']=args.concurrency
        if args.search_recheck is not None:
            cfg['performance']['search_recheck']=args.search_recheck
        if args.analysis_concurrency is not None:
            cfg['performance']['analysis_concurrency']=args.analysis_concurrency
        if args.no_template_cache:
            cfg['performance']['ppt_template_cache']=False
        if args.command in ('register','unregister'): return task_command(args.command)
        backend=Backend(ROOT)
        locks.enter_context(RunLock(backend.legacy_out/'v754_export.lock'))
        if args.command=='connect': return connect(cfg,backend)
        if args.command=='check':
            print('[공통 환경]',backend.check())
            print(f"[수집 대상] {len(selected(cfg))}개 카페 × {len(backend.words)}개 키워드 / 제목 검색")
            print(f"[동시 수집 설정] {cfg['collection']['concurrency']}개 / 1: 기존 순차, 2·3: 병렬")
            print(f"[공통 요청 간격] 병렬 수집 전체에서 {backend.webcfg['request_interval_seconds']}초 이상")
            show_performance(cfg)
            from v9.ai_provider import show_settings
            show_settings(backend.ai_settings, requested_concurrency=cfg['performance']['analysis_concurrency'])
            ps=subprocess.run(['powershell.exe','-NoProfile','-Command',
                "if (Test-Path 'Registry::HKEY_CLASSES_ROOT\\Outlook.Application\\CLSID') { exit 0 } else { exit 1 }"],timeout=30,capture_output=True)
            if ps.returncode: raise V8Error('OUTLOOK_NOT_REGISTERED','Classic Outlook COM 등록을 확인하지 못했습니다.')
            p=ROOT/'output_v9/connections.json';connections=read_json(p) if p.exists() else {}
            for c in selected(cfg):
                print(c['name'],'/ clubId:',connections.get(c['slug'],{}).get('club_id') or c['club_id'] or '31번으로 확인 필요')
            print('[수신자]','; '.join(cfg['mail']['to']))
            print('[확인 완료] 웹·AI·메일 호출 없음. 36번에서 짧은 기간을 시험하세요.')
            return 0
        send_mail=args.send_mail
        slugs=args.cafes.split(',') if args.cafes else None
        if args.command=='period' and not args.start:
            print('기간 지정 실행은 같은 기간을 다시 수집하며 예약 처리 완료 시각을 바꾸지 않습니다.')
            slugs=choose_cafes(cfg,','.join(str(i) for i,c in enumerate(cfg['cafes'],1) if c['enabled']))
            args.start=ask('수집 시작 YYYY-MM-DD HH:MM',cfg['schedule']['initial_start'] or '')
            args.end=ask('수집 종료 YYYY-MM-DD HH:MM',datetime.now(KST).strftime('%Y-%m-%d %H:%M'))
            if args.concurrency is None:
                cfg['collection']['concurrency']=choose_concurrency(cfg['collection']['concurrency'])
            print('이번 동시 수집 수는 이번 기간 실행에만 적용됩니다.')
            if args.search_recheck is None:
                cfg['performance']['search_recheck']=choose_recheck(cfg['performance']['search_recheck'])
            if args.analysis_concurrency is None:
                cfg['performance']['analysis_concurrency']=choose_analysis_concurrency(cfg['performance']['analysis_concurrency'])
            print('이번 검색·분석 선택은 이번 기간 실행에만 적용됩니다.')
            cfg,send_mail=prompt_test_mail(cfg)
        if args.command=='period' and not (args.start and args.end):
            raise V8Error('PERIOD_REQUIRED','시작·종료 시각을 모두 지정하세요.')
        from v8.vendor.outlook_mail import send_outlook_mail
        runner=Runner(ROOT,cfg,backend,send_outlook_mail)
        if args.command=='resolve-mail':
            identifier=args.identifier or choose_run()
            print('Outlook 보낸편지함·보낼편지함에서 해당 실행 번호를 확인하세요.')
            decision=args.decision
            if not decision:
                answer=ask('1: 발송/발송 대기 확인, 2: 미발송 확인','')
                if answer not in ('1','2'): raise V8Error('INVALID_INPUT','1 또는 2를 입력하세요.')
                decision='sent' if answer=='1' else 'not-sent'
            runner.resolve_mail(identifier,decision)
            return 0
        if args.command=='retry' and not args.identifier:
            p=ROOT/'output_v9/state.json'
            active=read_json(p).get('active') if p.exists() else None
            print('진행 중 예약:',active or '없음')
            value=ask('Enter: 예약 미처리·실패 구간 재시도 / p: 기간 지정 기록 선택','예약')
            if value.lower()=='p': args.identifier=choose_run()
            elif value!='예약': raise V8Error('INVALID_INPUT','Enter 또는 p를 입력하세요.')
        code,folder=runner.execute(start=args.start,end=args.end,slugs=slugs,send_mail=send_mail,
                                   retry=args.command=='retry',identifier=args.identifier)
        if notice is not None:
            if folder:
                notice.update(status=runner.last_record['status'],body=runner.summary_text(runner.last_record),
                              mail=runner.last_record['mail']['status'],run_folder=str(folder),record=str(folder/'run.json'))
            else: notice.update(status='waiting',body='진행할 새 예약 구간이 없습니다.')
        return code


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='V9 · 9개 자동차 카페 모니터링')
    parser.add_argument('command',choices=['setup','collection-setup','performance-setup','ppt-image-setup','ai-setup','connect','check','run','period','retry','status','register','unregister','resolve-mail','show-result'])
    parser.add_argument('--start');parser.add_argument('--end');parser.add_argument('--cafes')
    parser.add_argument('--identifier');parser.add_argument('--decision',choices=['sent','not-sent'])
    parser.add_argument('--send-mail',action='store_true');parser.add_argument('--no-result-window',action='store_true')
    parser.add_argument('--concurrency',type=int,choices=[1,2,3],help='이번 실행 동시 수집 수; 설정 파일은 유지')
    parser.add_argument('--search-recheck',choices=['adaptive','full'],help='이번 실행 검색 재확인 방식')
    parser.add_argument('--analysis-concurrency',type=int,choices=[1,2,3],help='이번 실행 AI 동시 분석 수')
    parser.add_argument('--no-template-cache',action='store_true',help='이번 실행은 PPT 양식을 새로 생성')
    args=parser.parse_args(argv)
    print(f'[V{__version__}]',args.command,flush=True)
    scheduled=args.command=='run' and not args.no_result_window
    notice=dict(started_at=stamp(),status='failed',body='') if scheduled else None
    path=None
    with ExitStack() as stack:
        if scheduled:
            folder=ROOT/'output_v9/notices';folder.mkdir(parents=True,exist_ok=True)
            import uuid
            path=folder/(datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8]+'.json')
            log=stack.enter_context(path.with_suffix('.log').open('w',encoding='utf-8'))
            stack.enter_context(redirect_stdout(Tee(sys.stdout,log)))
            stack.enter_context(redirect_stderr(Tee(sys.stderr,log)))
        try:
            code=run_command(args,notice)
        except (KeyboardInterrupt,EOFError):
            code=130
            print('[취소] 저장한 결과는 보존됩니다.')
            if notice is not None: notice.update(status='cancelled',body='사용자 취소')
        except Exception as exc:
            busy=getattr(exc,'code','')=='ALREADY_RUNNING'
            code=4 if busy else 1
            print('[실행 중·건너뜀]' if busy else '[중단]',getattr(exc,'code',type(exc).__name__),str(exc))
            if not busy: traceback.print_exc()
            if notice is not None: notice.update(status='skipped_busy' if busy else 'failed',body=str(exc))
        if notice is not None:
            # All browser/Office and execution locks have been released.
            from v8.result_notice import send_current_session_message
            notice.update(finished_at=stamp(),exit_code=code)
            text='V9 실행 결과: '+notice['status']+'\n'+notice['body']+'\n메일: '+notice.get('mail','발송 없음')
            text+='\n결과 폴더: '+notice.get('run_folder','없음')+'\n상세 결과: 40_show_result_v9.bat'
            write_json(path,notice);path.with_suffix('.txt').write_text(text,encoding='utf-8-sig')
            try: opened=send_current_session_message('V9 · 카페별 실행 결과',text) if os.name=='nt' else False
            except Exception: opened=False
            if not opened: print('[결과 알림 기록]',path.with_suffix('.txt'))
    return code


if __name__=='__main__': raise SystemExit(main())
