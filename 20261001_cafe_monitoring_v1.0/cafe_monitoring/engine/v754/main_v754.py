"""V7.5.4: 기간 수집 → 저장 결과 분석 → PowerPoint. 각 단계는 재실행 가능합니다."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import json
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v754.core import config, V7Error, read_json, write_json, capture_files
from v754.collect.collector import KST
from v754.collect.run_lock import RunLock
from v754.period import Window
from v754.period_config import load_period_config, keywords
from v754.period_sources import load_collection, load_period_source


@contextmanager
def timed(report, name, checkpoint):
    started = time.monotonic()
    try:
        yield
    finally:
        elapsed = round(time.monotonic() - started, 3)
        report.setdefault('timings_seconds', {})[name] = elapsed
        checkpoint()
        print(f'[소요 시간] {name} / {elapsed:.1f}초', flush=True)


def ask(label, default=''):
    value = input(f'{label} (Enter: {default or "전체"}, 취소: /q): ').strip()
    if value.lower() == '/q':
        raise V7Error('CANCELLED', '취소했습니다.')
    return value or default


def period_input(args, settings):
    now = datetime.now(KST)
    end_default = now.replace(second=0, microsecond=0)
    start = args.start or settings.get('period_start')
    end = args.end or settings.get('period_end')
    if args.prompt:
        print('게시글 작성 시각 기준 / 한국시간 / 시작 포함·종료 제외')
        start = start or ask('시작 YYYY-MM-DD HH:MM', (end_default - timedelta(days=1)).strftime('%Y-%m-%d %H:%M'))
        end = end or ask('종료 YYYY-MM-DD HH:MM', end_default.strftime('%Y-%m-%d %H:%M'))
    if not start or not end:
        raise V7Error('PERIOD_REQUIRED', '--start "YYYY-MM-DD HH:MM" --end "YYYY-MM-DD HH:MM" 또는 --prompt를 지정하세요.')
    window = Window.from_input(start, end, now=now)
    value = args.keywords
    if not value and args.prompt:
        value = ask('검색어 쉼표 구분', ', '.join(settings['keywords']))
    return window, keywords(value or settings['keywords'])


def new_folder(cfg, prefix):
    tag = datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
    folder = cfg['out_root'] / (prefix + tag)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def report_base(kind):
    return {'version': '7.5.4', 'build': '7.5.4.1', 'kind': kind, 'status': 'running', 'stage': 'PREPARE',
            'created_at': datetime.now(KST).isoformat(), 'api_calls': 0, 'analysis_cache_hits': 0,
            'items': [], 'selected_articles': 0, 'capture_failures': [], 'missing_capture_articles': [],
            'warnings': [], 'range_search_complete': False, 'articles_verified_complete': False,
            'collection_complete': False, 'reopened_structure_verified': False,
            'visual_check': 'not_run', 'semantic_review': 'not_reviewed',
            'same_count_policy': 'undefined_not_counted', 'timings_seconds': {}}


def select_run(cfg, args, filename):
    choices = sorted([p for p in cfg['out_root'].glob('*') if p.is_dir() and (p / filename).is_file()], reverse=True)
    if filename == 'summary.json':
        # 재생성 메뉴에는 저장 분석이 있는 실행만 표시합니다.
        usable = []
        for path in choices:
            try:
                if any(i.get('analysis_result_file') for i in read_json(path / filename).get('items', [])):
                    usable.append(path)
            except V7Error:
                continue
        choices = usable
    if args.run:
        match = next((p for p in choices if p.name == args.run), None)
        if match is None:
            raise V7Error('RUN_NOT_FOUND', 'output_v754 안에서 실행 ID를 찾지 못했습니다.')
        return match
    if not choices:
        raise V7Error('RUN_NOT_FOUND', '선택할 결과가 없습니다. 먼저 02_collect_period_v754.bat를 실행하세요.')
    if not args.prompt:
        raise V7Error('RUN_REQUIRED', '--run 실행ID 또는 --prompt를 지정하세요.')
    print('\n사용 가능한 V7.5.4 실행 (최근 순)')
    shown = choices[:30]
    for i, path in enumerate(shown, 1):
        try:
            data = read_json(path / filename)
            w = data.get('window', {})
            label = '수집 완료' if data.get('collection_complete') else '수집 미완료'
            print(f" {i}. {path.name} / {label} / {len(data.get('items', []))}개 / {w.get('start', '')} ~ {w.get('end', '')}")
        except V7Error:
            print(f' {i}. {path.name} / 기록 읽기 실패')
    answer = ask('번호 또는 실행 ID', '1')
    if answer.isdigit() and 1 <= int(answer) <= len(shown):
        return shown[int(answer) - 1]
    match = next((p for p in choices if p.name == answer), None)
    if not match:
        raise V7Error('RUN_NOT_FOUND', '목록에 표시된 실행을 선택하세요.')
    return match


def fail_report(folder, report, checkpoint, exc):
    report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
    report['error'] = {'code': getattr(exc, 'code', type(exc).__name__),
                       'message': str(exc) if hasattr(exc, 'code') else '실행 중 예외가 발생했습니다. 오류 유형과 실행 단계를 확인하세요.'}
    checkpoint()
    if (folder / 'search_audit.json').is_file():
        from v754.period_collect import write_collection_html
        write_collection_html(folder, report, read_json(folder / 'search_audit.json'))
    print(f"[실패 단계] {report['stage']} / {folder / 'summary.json'}", flush=True)


def export_items(items, cfg, folder, report, checkpoint):
    from v754.analysis_config import load_analysis_config
    from v754.analysis_prompts import make_spec
    from v754.analysis_engine import analyze_articles
    from v754.comparison import write_comparison
    from v754.powerpoint import check_office, render
    if not report.get('collection_complete'):
        raise V7Error('COLLECTION_INCOMPLETE', '기간 수집 미완료 상태에서는 분석·PPT 생성을 진행하지 않습니다.')
    if not items:
        report.update(status='completed_empty', stage='COMPLETE', analyzed_articles=0)
        checkpoint()
        print('[대상 없음] 해당 기간·검색어의 확인된 대상이 0개입니다. AI 호출·PPT 생성 없음.')
        return 0
    check_office()
    spec = make_spec(load_analysis_config(ROOT))
    report['complaint_policy'] = spec['complaint_definition']
    print(f"[AI 안내] 선정 {len(items)}개 / 최초 분석 최대 {len(items)}회 + 필요 시 재요약 글당 최대 1회 / 모델 {spec['model']}")
    print('동일 원문·모델·지침 캐시는 재사용합니다. V7.5.4.1은 요약 지침이 바뀌어 최초 분석이 새로 실행될 수 있습니다. 새 분석은 API 사용량이 발생합니다.')
    with timed(report, 'analysis', checkpoint):
        analyzed = analyze_articles(items, spec, folder, report, checkpoint)
    write_comparison(folder, report)
    if not analyzed:
        raise V7Error('NO_ANALYZED_ARTICLES', '사용 가능한 분석이 없습니다. analysis_audit.json을 확인하세요.')
    from v754.summary_repair import SummaryRepair
    resolver = SummaryRepair(spec, folder, report, checkpoint)
    with timed(report, 'powerpoint', checkpoint):
        render(analyzed, cfg, folder, report, checkpoint, summary_resolver=resolver)
    partial = (len(analyzed) != len(items) or bool(report['capture_failures'] or report['missing_capture_articles'] or report['warnings'])
               or any(a['capture_problem'] or a['metadata_warnings'] or a['analysis_issues'] or a.get('summary_pending') for a in analyzed))
    report['analysis_review_count'] = sum(bool(i.get('analysis_issues') or i.get('summary_pending')) for i in analyzed)
    report.update(status='partial' if partial else 'completed',
                  analyzed_articles=sum(bool(i.get('analysis_candidate')) for i in analyzed),
                  included_articles=len(analyzed),
                  pending_summary_articles=[i['id'] for i in analyzed if i.get('summary_pending')])
    checkpoint()
    write_comparison(folder, report)
    print(f"[PPT 저장] {report['pptx']}")
    print(f"[분석] 새 API {report['api_calls']}회 / 재사용 {report['analysis_cache_hits']}건")
    print(f"[다시 열기 확인] {report['slides']}장 / 분석 표·URL·메타데이터·그림 검증")
    print(f"[원문·분석 비교] {folder / 'comparison.html'}")
    print('직원 검토 전 초안입니다. 분류와 요약을 원문과 대조하세요.')
    return 2 if partial else 0


def do_collect(args, cfg):
    from v754.refresh import browser_config
    from v754.period_collect import collect_with_browser, write_collection_html
    settings = load_period_config(ROOT)
    window, words = period_input(args, settings)
    webcfg = browser_config(ROOT, cfg)
    if args.command == 'collect-export':
        from v754.powerpoint import check_office
        from v754.analysis_config import load_analysis_config
        check_office()
        load_analysis_config(ROOT)
    folder = new_folder(cfg, 'collect_')
    report = report_base('period_collection')
    report.update(window=window.record(), keywords=words, cafe_id=webcfg['cafe_id'],
                  search_scope='title', search_board='all', search_sort='latest')
    checkpoint = lambda: write_json(folder / 'summary.json', report)
    checkpoint()
    print(f'[출력 폴더] {folder}', flush=True)
    print(f'[기간] {window.start:%Y-%m-%d %H:%M} 이상 ~ {window.end:%Y-%m-%d %H:%M} 미만 / 한국시간')
    print(f"[검색] {', '.join(words)} / 제목만 / 전체 게시판 / 최신순 / 고정 글 수 제한 없음")
    print(f"[탐색 한도] 검색어당 {settings['max_pages_per_keyword']}페이지 / 한도에서 멈추면 수집 미완료")
    try:
        with timed(report, 'collection', checkpoint):
            items = collect_with_browser(cfg, settings, webcfg, folder, report, checkpoint, window, words)
        if not report['collection_complete']:
            report['status'] = 'collection_incomplete'
            code = 2
            print('[수집 미완료] 검색·작성 시각 확인을 끝내지 못했습니다. AI 분석은 시작하지 않았습니다.')
        else:
            problems = any(i['capture_problem'] or i['metadata_warnings'] for i in items)
            report['status'] = 'collected_with_issues' if problems else ('collected' if items else 'collected_empty')
            print(f'[기간 수집 완료] 대상 {len(items)}개 / AI 호출 0', flush=True)
            code = 2 if problems else 0
        checkpoint()
        write_collection_html(folder, report, read_json(folder / 'search_audit.json'))
        print(f"[수집 대조표] {folder / 'collection.html'}")
        if args.command == 'collect-export' and report['collection_complete']:
            code = export_items(items, cfg, folder, report, checkpoint)
        return code
    except BaseException as exc:
        fail_report(folder, report, checkpoint, exc)
        raise
    finally:
        print(f"[결과 기록] {folder / 'summary.json'}", flush=True)


def do_saved_export(args, cfg):
    prior = select_run(cfg, args, 'collection.json')
    data, items = load_collection(prior)
    folder = new_folder(cfg, 'export_')
    report = report_base('saved_collection_export')
    report.update(collection_from=str(prior), collection_complete=True, range_search_complete=True,
                  articles_verified_complete=True, window=data['window'], keywords=data['keywords'],
                  cafe_id=data['cafe_id'], selected_articles=len(items))
    checkpoint = lambda: write_json(folder / 'summary.json', report)
    checkpoint()
    print(f'[저장 수집 사용] {prior.name} / {len(items)}개 / 웹 재방문 없음')
    print(f'[출력 폴더] {folder}')
    try:
        return export_items(items, cfg, folder, report, checkpoint)
    except BaseException as exc:
        fail_report(folder, report, checkpoint, exc)
        raise
    finally:
        print(f"[결과 기록] {folder / 'summary.json'}")


def rebuild(args, cfg):
    from v754.analysis_engine import verify_display_artifact
    from v754.powerpoint import check_office, render
    prior = select_run(cfg, args, 'summary.json')
    old = read_json(prior / 'summary.json')
    if old.get('version') != '7.5.4' or not old.get('collection_complete'):
        raise V7Error('COLLECTION_INCOMPLETE', 'V7.5.4 기간 수집 완료 결과만 재생성할 수 있습니다.')
    prior_items = deepcopy(old.get('items', []))
    items = prior_items
    if not items:
        raise V7Error('NO_ANALYZED_ARTICLES', '해당 실행에 저장된 분석이 없습니다. 03_export_saved_v754.bat를 사용하세요.')
    check_office()
    for item in prior_items:
        verify_display_artifact(item)
        source = load_period_source(item)
        p = Path(item['refreshed_source_file'])
        caps, notes = capture_files({'source': {**source, 'source_file': str(p)}, 'capture': source.get('capture', {})}, p.parent)
        item.update(captures=caps, capture_notes=notes,
                    capture_problem=not caps or any('DRM 보호 파일' not in n for n in notes))
    folder = new_folder(cfg, 'rebuild_')
    report = report_base('rebuild')
    report.update(items=items, rebuild_from=str(prior), collection_complete=True,
                  range_search_complete=True, articles_verified_complete=True,
                  window=old['window'], keywords=old['keywords'], cafe_id=old['cafe_id'],
                  selected_articles=old['selected_articles'])
    checkpoint = lambda: write_json(folder / 'summary.json', report)
    checkpoint()
    try:
        # 저장 분석을 새 표시 정책으로 옮깁니다. 이전 결과는 수정하지 않고 API도 호출하지 않습니다.
        from v754.analysis_engine import (build_display, pending_display, persist_display,
                                         source_identity, update_display_record)
        from v754.analysis_schema import validate_candidate, semantic_issues
        collection_path = Path(old['collection_from']) if old.get('collection_from') else prior
        if (collection_path / 'collection.json').is_file():
            _, items = load_collection(collection_path)
        by_id = {(i['cafe_id'], i['id']): i for i in prior_items}
        refreshed = []
        (folder / 'analysis').mkdir()
        for item in items:
            source = load_period_source(item)
            prior_item = by_id.get((item['cafe_id'], item['id']))
            candidate = prior_item.get('analysis_candidate') if prior_item else None
            if candidate:
                if prior_item.get('analysis_input_sha256') != source_identity(source)['input_sha256']:
                    raise V7Error('SOURCE_CHANGED', '저장 분석과 재생성할 수집 원문이 다릅니다.')
                candidate, bindings = validate_candidate(candidate, source)
                current = build_display(item, source, candidate, bindings, semantic_issues(candidate, bindings))
            else:
                current = pending_display(item, source, 'NO_SAVED_ANALYSIS', '저장 분석이 없어 게시글과 캡처를 유지했습니다. API 호출은 하지 않았습니다.')
            path = folder / 'analysis' / f"{item['cafe_id']}_{item['id']}_result.json"
            persist_display(current, path, {'identity': source_identity(source), 'candidate': candidate,
                            'migrated_from': prior_item.get('analysis_result_file') if prior_item else None})
            update_display_record(current, folder, report, reason='OFFLINE_REBUILD')
            refreshed.append(current)
        items = refreshed
        report['items'] = items
        report['collection_from'] = str(collection_path) if (collection_path / 'collection.json').is_file() else None
        with timed(report, 'powerpoint', checkpoint):
            render(items, cfg, folder, report, checkpoint)
        from v754.comparison import write_comparison
        write_comparison(folder, report)
        partial = (len(items) != report['selected_articles'] or bool(report['capture_failures'] or report['missing_capture_articles'] or report['warnings'])
                   or any(i['analysis_issues'] or i['metadata_warnings'] or i.get('summary_pending') or i.get('capture_problem') for i in items))
        report['status'] = 'partial' if partial else 'completed'
        report['included_articles'] = len(items)
        report['pending_summary_articles'] = [i['id'] for i in items if i.get('summary_pending')]
        checkpoint()
        print(f"[PPT 재생성] {report['pptx']} / 웹 재방문·AI 호출 없음")
        return 2 if partial else 0
    except BaseException as exc:
        fail_report(folder, report, checkpoint, exc)
        raise


def main():
    parser = argparse.ArgumentParser(description='V7.5.4 한국시간 기간 수집·분석·PPT 생성')
    parser.add_argument('command', choices=('check', 'collect', 'export-saved', 'collect-export', 'rebuild'))
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--keywords')
    parser.add_argument('--run')
    parser.add_argument('--prompt', action='store_true')
    args = parser.parse_args()
    print(f'[V7.5.4.1] {args.command}', flush=True)
    try:
        cfg = config(ROOT)
        cfg['project_root'] = ROOT
        if cfg['out_root'] == ROOT.resolve():
            raise V7Error('CONFIG_ERROR', '출력 폴더를 프로젝트 루트와 분리하세요.')
        if args.command == 'check':
            from v754.refresh import browser_config
            from v754.analysis_config import load_analysis_config, load_api_key
            from v754.powerpoint import check_office
            from v754.api_transport import certificate_policy
            import playwright.sync_api
            import openai
            print(check_office())
            settings = load_period_config(ROOT)
            webcfg = browser_config(ROOT, cfg)
            ai = load_analysis_config(ROOT)
            print(f"카페: {webcfg['cafe_id']} / 브라우저: {webcfg['browser']} / 기존 V5 로그인 프로필")
            print(f"검색어: {', '.join(settings['keywords'])} / 제목만 / 전체 게시판 / 최신순")
            print(f"검색어당 최대 {settings['max_pages_per_keyword']}페이지 / 한도 도달은 미완료")
            print(f"모델: {ai['model']} / API 키: {'설정됨' if load_api_key() else '미설정'} / 인증서: {certificate_policy()}")
            if certificate_policy() == 'windows_system_truststore':
                from v754.api_transport import windows_ssl_context
                windows_ssl_context()
            print(f"카페 표시명 {len(cfg['cafe_names'])}개 / 출력: {cfg['out_root']}")
            print('CHECK_OK: 설정·모듈·PowerPoint 등록 확인. 웹 수집·AI 호출·PPT 생성은 실행하지 않았습니다.')
            return 0
        cfg['out_root'].mkdir(parents=True, exist_ok=True)
        with RunLock(cfg['out_root'] / 'v754_export.lock'):
            if args.command in ('collect', 'collect-export'):
                return do_collect(args, cfg)
            if args.command == 'export-saved':
                return do_saved_export(args, cfg)
            return rebuild(args, cfg)
    except (KeyboardInterrupt, EOFError):
        print('취소했습니다. 저장된 결과는 보존됩니다.')
        return 130
    except Exception as exc:
        code = getattr(exc, 'code', type(exc).__name__)
        if code == 'CANCELLED':
            print('취소했습니다. 기존 결과는 보존됩니다.')
            return 130
        message = str(exc) if hasattr(exc, 'code') else '실행 환경·설치 상태를 확인하세요. 저장된 summary.json의 실패 단계를 보내 주세요.'
        print(f'[중단: {code}] {message}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
