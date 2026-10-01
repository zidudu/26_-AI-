"""Reopen only a selected V6 run's posts for current screenshots. No API / DB edits."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Prefer the V5 collector over the older collector.py in the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector import CollectorError, KST, Target, VERSION, collect_article, save_article
from settings import read_settings
from run_lock import RunLock
from run_report import atomic_json
from v6.common import digest, text_hash
from v6.settings import load_config


def select_run(cfg, run_id):
    paths = sorted((cfg['output_dir']/'runs').glob('*/manifest.json'), reverse=True)
    if run_id:
        paths = [p for p in paths if p.parent.name == run_id]
    for p in paths:
        m = json.loads(p.read_text(encoding='utf-8-sig'))
        if not isinstance(m.get('jobs'), list) or not m['jobs']:
            continue
        articles, seen = [], set()
        for job in m['jobs']:
            a = job['article']
            if (a['cafe_id'] != cfg['cafe_id'] or not str(a['article_id']).isdigit()
                    or a['body_sha256'] != text_hash(a['body'])
                    or a['input_sha256'] != digest({k:a[k] for k in ('title','body','written_at')})):
                raise CollectorError('INVALID_SNAPSHOT', '저장된 실행의 원문과 식별 정보를 확인하세요.')
            if a['article_id'] not in seen:
                articles.append(a); seen.add(a['article_id'])
        if len(articles)>100:
            raise CollectorError('CAPTURE_LIMIT','한 번에 최대 100개까지 캡처할 수 있습니다.')
        return p.parent.name, articles
    raise CollectorError('NO_SAVED_RUN','캡처할 분석 실행 ID를 찾지 못했습니다.')


def capture_run(cfg, articles, folder, page, timeout_error):
    report = {'scope':'selected_saved_run_posts','api_calls':0,'items':[],'status':'running',
              'note':'현재 열리는 화면을 새로 캡처합니다. 과거 실행 시점의 화면을 재현하지 않습니다.'}
    atomic_json(folder/'capture_summary.json',report)
    try:
        for i, original in enumerate(articles,1):
            aid = original['article_id']
            print(f'[{i}/{len(articles)}] 게시글 캡처 / {aid}')
            try:
                article=collect_article(page,Target(cfg['cafe_id'],aid),{**cfg,'capture_output_dir':folder},timeout_error)
                article['matched_keywords']=original.get('matched_keywords',[])
                same=digest({k:article[k] for k in ('title','body','written_at')})==original['input_sha256']
                article['capture']['matches_saved_input']=same
                path=save_article(folder,article)
                item={'article_id':aid,'status':article['capture']['status'],'source_file':path.name,
                      'capture_files':len(article['capture']['files']),'matches_saved_input':same,
                      'warnings':article['capture'].get('warnings',[]),
                      'diagnostics':article['capture'].get('diagnostics',[])}
                print(f"  {item['status']} / 캡처 {item['capture_files']}장 / 이전 입력 일치 {same}")
                if item['warnings']:
                    print('  [캡처 경고] ' + ', '.join(item['warnings']))
                for detail in item['diagnostics']:
                    print(f"  [캡처 단계] {detail['stage']} / {detail.get('exception_type', detail.get('code', ''))}")
            except CollectorError as exc:
                item={'article_id':aid,'status':'unavailable','code':exc.code}
                print(f'  건너뜀 / {exc.code}')
                if exc.code in ('LOGIN_REQUIRED','AUTH_REQUIRED','REQUEST_BLOCKED'):
                    report['items'].append(item)
                    report['stop_code']=exc.code
                    break
            report['items'].append(item)
            atomic_json(folder/'capture_summary.json',report)
            if i<len(articles): page.wait_for_timeout(cfg.get('request_interval_seconds',2)*1000)
        report['status']='partial' if len(report['items'])<len(articles) or any(i['status']!='captured' for i in report['items']) else 'completed'
    except (KeyboardInterrupt,EOFError):
        report['status']='cancelled'
    finally:
        report['requested']=len(articles)
        report['unprocessed']=len(articles)-len(report['items'])
        atomic_json(folder/'capture_summary.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description='저장된 V6 실행의 게시글 캡처 추가')
    p.add_argument('--run-id');p.add_argument('--prompt',action='store_true')
    p.add_argument('--config',type=Path);p.add_argument('--config-v5',type=Path,default=ROOT/'config_v5.json')
    args=p.parse_args(argv)
    try:
        print(f'[캡처 도구 V{VERSION}]')
        if args.prompt and not args.run_id:
            answer=input('캡처할 실행 ID (Enter: 최근 실행, 취소: /q): ').strip()
            if answer.lower()=='/q':return 0
            args.run_id=answer or None
        v6cfg=load_config(args.config)
        cfg,_=read_settings(args.config_v5)
        if cfg['cafe_id']!=v6cfg['cafe_id']:
            raise CollectorError('CAFE_MISMATCH','V5와 V6의 카페 설정이 다릅니다.')
        run_id,articles=select_run(v6cfg,args.run_id)
        print(f'[캡처 추가] 실행 {run_id} / {len(articles)}개 / API 호출 없음')
        folder=cfg['output_dir']/('capture_'+datetime.now(KST).strftime('%Y%m%d_%H%M%S_')+uuid4().hex[:8])
        folder.mkdir(parents=True,exist_ok=False)
        from playwright.sync_api import sync_playwright,TimeoutError as PWTimeout
        with ExitStack() as stack:
            stack.enter_context(RunLock(cfg['profile_lock']))
            stack.enter_context(RunLock(str(cfg['history_db'])+'.lock'))
            pw=stack.enter_context(sync_playwright())
            options={'user_data_dir':str(cfg['profile_dir']),'headless':False,'locale':'ko-KR',
                     'timezone_id':'Asia/Seoul','viewport':{'width':1365,'height':900}}
            if cfg['browser']!='chromium':options['channel']=cfg['browser']
            context=pw.chromium.launch_persistent_context(**options)
            stack.callback(context.close)
            report=capture_run(cfg,articles,folder,context.new_page(),PWTimeout)
        print(f"[캡처 결과] {report['status']} / 요약: {folder/'capture_summary.json'}")
        print('13_convert_saved_v6.bat에서 같은 실행 ID를 입력하면 초안에 캡처를 연결합니다.')
        print('현재 본문이 예전 입력과 달라진 글의 캡처는 예전 분석에 연결하지 않습니다.')
        if cfg['output_dir'] not in v6cfg['input_dirs'] and not any(d in cfg['output_dir'].parents for d in v6cfg['input_dirs']):
            print('[설정 확인] config_v6.json input_dirs에 V5 output_dir를 추가해야 새 캡처를 찾습니다.')
        return 0 if report['status']=='completed' else 3
    except (KeyboardInterrupt,EOFError):
        print('캡처를 중단했습니다. 저장된 자료는 유지됩니다.');return 130
    except Exception as exc:
        print(f"[{getattr(exc,'code','CAPTURE_TOOL_ERROR')}] 브라우저·설정·실행 ID를 확인하세요. 다른 수집기와 함께 실행하지 마세요.")
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        if frames:
            last = frames[-1]
            print(f'[오류 위치] {type(exc).__name__} / {Path(last.filename).name}:{last.lineno} / {last.name}')
        return 1


if __name__=='__main__':raise SystemExit(main())
