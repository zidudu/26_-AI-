"""Rebuild saved V9 article analyses; optional cafe-only AI, never collection/mail."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import argparse
import hashlib
import os
import re
import sys
import traceback
import uuid

from v754.core import V7Error, capture_files, config, read_json, write_json
from v8.pipeline import Tee
from v9 import __version__

DEFAULT_RUN = None
KST = timezone(timedelta(hours=9))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_for(root, run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r'(?:period|batch)_\d{8}_\d{6}_\d{6}_[a-f0-9]{8}', run_id):
        raise ValueError('실행 ID 형식을 확인하세요.')
    run_folder = root / 'output_v9' / ('period' if run_id.startswith('period_') else 'runs') / run_id
    record = read_json(run_folder / 'run.json')
    if record.get('id') != run_id or record.get('mode') not in ('period','scheduled'):
        raise ValueError('실행 ID 또는 실행 종류가 일치하지 않습니다.')
    if record.get('stage') not in ('EXPORT','COMPLETE','MAIL'):
        raise ValueError('분석 또는 내보내기 결과가 있는 실행을 선택하세요.')
    exports = record.get('exports', [])
    if not exports or not re.fullmatch(r'export_[a-f0-9]{12}', str(exports[-1])):
        raise ValueError('저장 분석의 내보내기 폴더를 확인하지 못했습니다.')
    source = (run_folder / exports[-1] / 'summary.json').resolve()
    if not source.is_relative_to(run_folder.resolve()):
        raise ValueError('저장 분석 경로가 실행 폴더 밖입니다.')
    artifact=record.get('artifact') or {}
    if artifact.get('summary')==str(source.relative_to(run_folder.resolve())) and artifact.get('summary_sha256'):
        if sha(source)!=artifact['summary_sha256']:
            raise ValueError('완료된 실행의 저장 분석 해시가 다릅니다.')
    return record, source


def prepare_report(saved, record):
    from v8.analysis_engine import load_bound_source, source_identity, verify_display_artifact
    items = saved.get('items')
    if (saved.get('collection_complete') is not True or not isinstance(items, list)
            or len(items) != saved.get('selected_articles')
            or saved.get('analysis_remaining',0) != 0):
        raise ValueError('선정 게시글의 저장 분석이 모두 완료된 상태가 아닙니다.')
    expected = [key for cafe in record['cafes'] if cafe.get('collection') or cafe.get('status') in ('collected','completed','completed_empty','partial')
                for key in cafe.get('article_keys', [])]
    actual = [str(item['cafe_id']) + ':' + str(item['id']) for item in items]
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError('실행 기록의 게시글과 저장 분석 대상이 다릅니다.')
    for item in items:
        verify_display_artifact(item)
        source, _ = load_bound_source(item)
        if source_identity(source) != {'cafe_id': item['cafe_id'], 'article_id': item['id'],
                                       'input_sha256': item['analysis_input_sha256']}:
            raise ValueError('분석 입력과 저장 원문이 다릅니다.')
        if item.get('source_kind') == 'period_collection':
            captures, _ = capture_files({'source': {**source, 'source_file': item['refreshed_source_file']},
                                        'capture': source.get('capture', {})},
                                       Path(item['refreshed_source_file']).parent)
            identity = lambda c: (c['index'], str(Path(c['path']).resolve()), c.get('original_sha256'))
            if [identity(c) for c in captures] != [identity(c) for c in item['captures']]:
                raise ValueError('저장된 캡처 파일이 변경되거나 누락됐습니다.')
    report = {'version': '9.3.1', 'application_version': __version__, 'kind': 'saved_ppt_rebuild', 'status': 'running',
              'stage': 'PREPARE', 'items': deepcopy(items), 'selected_articles': len(items),
              'api_calls': 0, 'analysis_cache_hits': 0, 'reused_analysis_count': len(items),
              'collection_requested': False, 'ai_requested': False, 'mail_requested': False,
              'mail': {'status': 'disabled'}, 'reopened_structure_verified': False,
              'warnings': deepcopy(saved.get('warnings', [])),
              'capture_failures': deepcopy(saved.get('capture_failures', [])),
              'missing_capture_articles': deepcopy(saved.get('missing_capture_articles', [])),
              'source_run_id': record['id'], 'source_cafes': deepcopy(record['cafes']),
              'timings_seconds': {}}
    if saved.get('cafe_summaries'):report['cafe_summaries']=deepcopy(saved['cafe_summaries'])
    return report


def no_new_analysis(article, reason):
    if reason == 'SEMANTIC_REVIEW':
        print(f"[저장 요약 유지] {article['cafe_id']}:{article['id']} / 검토 표시 유지·추가 AI 없음", flush=True)
        return False
    # Do not silently shorten or replace a saved summary to force a successful export.
    raise V7Error('REBUILD_NEEDS_ANALYSIS',
                  f"게시글 {article['id']}: {reason}. 추가 AI 없이 저장된 요약을 배치하지 못했습니다.")


def execute(root, run_id, folder, *, generate_cafe_summaries=False):
    from v8.backend import verify_base
    from v754.collect.run_lock import RunLock
    from v9.powerpoint import render

    verify_base(root)
    for rel, checksum in read_json(Path(__file__).with_name('engine_manifest.json')).items():
        if not (root / rel).is_file() or sha(root / rel) != checksum:
            raise ValueError('검증한 V8.2 엔진과 다릅니다: ' + rel)
    cfg = config(root)
    legacy_out = cfg['out_root']
    cfg.update(project_root=root, out_root=folder,
               v93_performance={'ppt_template_cache': True})
    with ExitStack() as locks:
        locks.enter_context(RunLock(root / 'output_v8/v8.lock'))
        locks.enter_context(RunLock(legacy_out / 'v754_export.lock'))
        record, source = source_for(root, run_id)
        saved=read_json(source)
        report = prepare_report(saved, record)
        from v9.dashboard_data import context
        from v754.period_config import load_period_config
        words=(saved.get('dashboard_stats') or {}).get('keywords') or record.get('keyword_order')
        if words is None:
            words=load_period_config(root)['keywords']
            if record.get('keywords') and set(words)!=set(record['keywords']):
                raise ValueError('당시 검색어와 현재 설정이 다릅니다. 당시 검색어 설정을 복원하세요.')
        context(cfg,report,report['items'],record['cafes'],words)
        report['dashboard_settings']=deepcopy(cfg['v96_dashboard'])
        report.update(source_summary=str(source), source_summary_sha256=sha(source))
        checkpoint = lambda: write_json(folder / 'ppt_report.json', report)
        checkpoint()
        print(f"[저장 분석 재사용] {len(report['items'])}건 / API 0회", flush=True)
        failed = [c['name'] for c in record['cafes'] if c.get('status') == 'failed']
        if failed:
            print('[수집 미완료·상세 게시글 제외·요약 위치 유지] ' + ', '.join(failed), flush=True)
        try:
            if (generate_cafe_summaries and report['items'] and cfg['v96_dashboard']['enabled']
                    and cfg['v96_dashboard']['cafe_summary_ai']):
                from v9.ai_provider import analysis_binding, settings_for_record
                from v9.dashboard_ai import prepare
                report['ai_settings']=settings_for_record(record)
                report['ai_requested']=True
                with analysis_binding(root,report['ai_settings'],folder) as (spec,factory):
                    def dashboard_resolver(articles):
                        prepare(articles,cfg,folder,report,checkpoint,spec=spec,factory=factory)
                    render(report['items'],cfg,folder,report,checkpoint,
                           summary_resolver=no_new_analysis,dashboard_resolver=dashboard_resolver)
            else:
                render(report['items'], cfg, folder, report, checkpoint, summary_resolver=no_new_analysis)
            if report.get('reopened_structure_verified') is not True:
                raise ValueError('PPT 재열기 검증이 완료되지 않았습니다.')
            ppt = Path(report.get('pptx', '')).resolve()
            if ppt.parent != folder.resolve() or not ppt.is_file() or ppt.stat().st_size == 0:
                raise ValueError('재작성한 PPT 파일을 확인하지 못했습니다.')
            report['review_count'] = sum(bool(a.get('summary_pending') or a.get('analysis_issues'))
                                         for a in report['items'])
            report['status'] = 'partial' if (failed or report['review_count'] or report['warnings']
                or report['capture_failures'] or report['missing_capture_articles']
                or any(a.get('capture_problem') or a.get('metadata_warnings') for a in report['items'])) else 'completed'
            checkpoint()
            print('[PPT 재작성·재열기 검증 완료]', report['status'], flush=True)
            print('[PPT]', ppt, flush=True)
            print(f"[게시글 추가 AI] 0회 / [카페 요약 추가 AI] {report.get('cafe_summary_calls',0)}회 / [메일] 발송 안 함", flush=True)
            print('기존 실패 기록과 예약 처리 기록은 그대로 유지됩니다.', flush=True)
            return report
        except BaseException as exc:
            report.update(status='failed', error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)})
            checkpoint()
            raise


def choose_run(root):
    choices=[]
    for branch in ('period','runs'):
        for path in (root/'output_v9'/branch).glob('*/run.json'):
            try:
                record,source=source_for(root,path.parent.name)
                saved=read_json(source)
                if (saved.get('collection_complete') is True and isinstance(saved.get('items'),list)
                        and len(saved['items'])==saved.get('selected_articles') and saved.get('analysis_remaining',0)==0):
                    choices.append((record['id'],len(saved['items']),record.get('status','')))
            except Exception:continue
    choices.sort(key=lambda r:r[0].split('_',1)[1],reverse=True)
    if not choices:raise ValueError('재작성할 저장 분석이 없습니다.')
    for i,(rid,count,state) in enumerate(choices[:20],1):print(f'{i}. {rid} / {count}건 / {state}')
    value=input('대상 번호 (Enter: 최신 1번, 취소: /q): ').strip()
    if value=='/q':raise ValueError('취소했습니다.')
    index=int(value or '1')-1
    if not 0<=index<min(len(choices),20):raise ValueError('목록의 번호를 선택하세요.')
    return choices[index][0]

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', default=DEFAULT_RUN)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--generate-cafe-summaries',action='store_true',help='저장 분석으로 카페 요약만 추가 AI 생성')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if args.run is None:
        try:args.run=choose_run(root)
        except (ValueError,EOFError,KeyboardInterrupt) as exc:
            print('[재작성 취소]',str(exc));return 1
    stamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')
    folder = root / 'output_v93_rebuild' / ('rebuild_' + stamp + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    code = 1
    with (folder / 'rebuild.log').open('x', encoding='utf-8') as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            print(f'[V{__version__} PPT 재작성] 저장 분석 사용 / 재수집·게시글 재분석·메일 없음')
            print('[카페 요약 AI]', '새 요약 생성 허용·유효 캐시 재사용' if args.generate_cafe_summaries else '호출 안 함·저장 요약 사용')
            print('[대상 실행]', args.run)
            print('[결과 폴더]', folder)
            try:
                report = execute(root, args.run, folder,generate_cafe_summaries=args.generate_cafe_summaries)
                code = 2 if report['status'] == 'partial' else 0
            except BaseException as exc:
                tb = traceback.format_exc()
                (folder / 'error_traceback.txt').write_text(tb, encoding='utf-8')
                print('[재작성 실패]', getattr(exc, 'code', type(exc).__name__), str(exc))
                print(tb)
    if os.name == 'nt' and not args.no_open:
        try: os.startfile(str(folder))
        except OSError: pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
