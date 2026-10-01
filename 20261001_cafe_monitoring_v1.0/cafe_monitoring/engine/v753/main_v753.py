"""회사 PC용 V7.5.3 실행기: 재수집 + 근거 기반 새 분석 + 편집 가능한 PPT."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v753 import VERSION
from v753.core import V7Error, config, load_articles, run_choices, read_json, write_json


@contextmanager
def export_lock(out_root):
    out_root.mkdir(parents=True, exist_ok=True)
    with (out_root / 'v753_export.lock').open('a+b') as f:
        if f.tell() == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise V7Error('ALREADY_RUNNING', '다른 V7.5.3 작업이 실행 중입니다.') from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def select_folder(choices, requested, prompt, title):
    if not choices:
        raise V7Error('RUN_NOT_FOUND', '선택할 실행 결과가 없습니다.')
    if requested:
        selected = next((p for p in choices if p.name == requested), None)
        if selected is None:
            raise V7Error('RUN_NOT_FOUND', '선택한 실행 ID가 없습니다.')
        return selected
    if not prompt:
        raise V7Error('RUN_REQUIRED', '--run 실행ID 또는 --prompt를 지정하세요.')
    print('\n' + title)
    for i, p in enumerate(choices[:20], 1):
        print(f' {i:2}. {p.name}')
    answer = input('번호 또는 실행 ID (Enter: 1, 취소: /q): ').strip()
    if answer.lower() == '/q':
        raise V7Error('CANCELLED', '취소했습니다.')
    if not answer:
        return choices[0]
    if answer.isdigit() and 1 <= int(answer) <= min(20, len(choices)):
        return choices[int(answer) - 1]
    return select_folder(choices, answer, False, title)


def select_articles(items, args):
    if args.article:
        matches = [a for a in items if a['id'] == args.article]
        if not matches:
            raise V7Error('ARTICLE_NOT_FOUND', '선택한 실행에서 게시글 ID를 찾지 못했습니다.')
        return matches
    if args.command == 'test':
        if not args.prompt:
            return items[:1]
        print('\n테스트할 게시글')
        for i, item in enumerate(items, 1):
            print(f" {i}. {item['id']} / {item['title']}")
        answer = input('게시글 번호 (Enter: 1, 취소: /q): ').strip()
        if answer.lower() == '/q':
            raise V7Error('CANCELLED', '취소했습니다.')
        if not answer:
            answer = '1'
        if not answer.isdigit() or not 1 <= int(answer) <= len(items):
            raise V7Error('COUNT_INVALID', '목록에 표시된 게시글 번호를 입력하세요.')
        return [items[int(answer) - 1]]
    count = args.count
    if count is None and args.prompt:
        answer = input(f'PPT로 만들 글 수 1~{len(items)} (Enter: 전체, 취소: /q): ').strip()
        if answer.lower() == '/q':
            raise V7Error('CANCELLED', '취소했습니다.')
        if answer and not answer.isdigit():
            raise V7Error('COUNT_INVALID', '글 수는 정수로 입력하세요.')
        count = int(answer) if answer else len(items)
    count = len(items) if count is None else count
    if not 1 <= count <= min(100, len(items)):
        raise V7Error('COUNT_INVALID', '유효한 글 중 최대 100개까지 선택할 수 있습니다.')
    return items[:count]


def new_folder(cfg, prefix=''):
    tag = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
    path = cfg['out_root'] / (prefix + tag)
    path.mkdir(exist_ok=False)
    return path


def do_export(args, cfg):
    from v753.analysis_config import load_analysis_config
    from v753.analysis_prompts import make_spec
    from v753.analysis_engine import analyze_articles
    from v753.comparison import inspect_v6, write_comparison
    from v753.powerpoint import check_office, render
    from v753.refresh import browser_config, refresh_with_browser
    check_office()
    browser_config(ROOT, cfg)
    spec = make_spec(load_analysis_config(ROOT))
    source_folder = select_folder(run_choices(cfg), args.run, args.prompt, '사용 가능한 V6 실행 (최근 순)')
    articles, errors, basis = load_articles(source_folder, cfg, ROOT)
    if not articles:
        raise V7Error('NO_VALID_DRAFTS', '변환할 유효한 V6 초안이 없습니다.')
    print(f'[V6 원본] 유효 {len(articles)}개 / 제외 {len(errors)}개')
    articles = select_articles(articles, args)
    print(f"[이번 선정] {len(articles)}개 / 새 AI 호출 최대 {len(articles)}회 / 모델 {spec['model']}")
    print('[AI 안내] API 사용량이 발생합니다. 같은 원문·모델·지침의 저장 분석은 재사용합니다.')
    if args.reanalyze:
        print('[새 분석 지정] --reanalyze: 기존 캐시가 있어도 새 API 호출을 수행합니다.')
    print('[불만 구분] 증상 중심 검토용 / 동일건수는 기준 미정으로 - 유지')
    print('[배치] 캡처 분할 없음 / 슬라이드당 최대 2장 / 1장은 가운데 배치')
    with export_lock(cfg['out_root']):
        folder = new_folder(cfg, 'test_' if args.command == 'test' else '')
        report = {'version': VERSION, 'v6_run_id': source_folder.name, 'selection_basis': basis,
            'status': 'running', 'stage': 'PREPARE', 'created_at': datetime.now().astimezone().isoformat(),
            'selected_articles': len(articles), 'api_calls': 0, 'source_errors': errors,
            'capture_failures': [], 'missing_capture_articles': [], 'warnings': [],
            'visual_check': 'not_run', 'semantic_review': 'not_reviewed', 'reopened_structure_verified': False,
            'layout': 'a4_landscape_whole_capture', 'original_company_template_used': False,
            'items': [], 'refresh_results': [], 'complaint_policy': spec['complaint_definition'],
            'same_count_policy': 'undefined_not_counted'}
        checkpoint = lambda: write_json(folder / 'summary.json', report)
        checkpoint()
        write_json(folder / 'v6_mapping_audit.json', {'items': inspect_v6(articles)})
        print(f'[출력 폴더] {folder}', flush=True)
        try:
            refreshed = refresh_with_browser(articles, cfg, folder, report, checkpoint, ROOT)
            if not refreshed:
                raise V7Error('NO_REFRESHED_ARTICLES', '원문 일치가 확인된 재수집 결과가 없습니다.')
            analyzed = analyze_articles(refreshed, spec, folder, report, checkpoint, force=args.reanalyze)
            write_comparison(folder, report)
            if not analyzed:
                raise V7Error('NO_ANALYZED_ARTICLES', '사용 가능한 새 분석이 없습니다. analysis_audit.json을 확인하세요.')
            render(analyzed, cfg, folder, report, checkpoint)
            partial = bool(errors or report['capture_failures'] or report['missing_capture_articles'] or report['warnings']
                or any(a['capture_problem'] or a['metadata_warnings'] or a['analysis_issues'] for a in analyzed)
                or len(analyzed) != report['selected_articles'])
            report['status'] = 'partial' if partial else 'completed'
            report['analyzed_articles'] = len(analyzed)
            checkpoint()
            write_comparison(folder, report)
            print(f"\n[PPT 저장] {report['pptx']}")
            print(f"[분석] 새 API {report['api_calls']}회 / 재사용 {report['analysis_cache_hits']}건")
            print(f"[다시 열기 확인] {report['slides']}장 / 분석 표·URL·메타데이터·그림 검증")
            print(f"[원문·이전·새 분석 비교] {folder / 'comparison.html'}")
            print(f"[결과 기록] {folder / 'summary.json'}")
            print('직원 검토 전 초안입니다. EGR의 시간 표현과 불만 구분을 원문과 대조하세요.')
            if partial:
                print('[일부 확인 필요] 제외·검사 지적 항목이 결과 기록에 있습니다.')
            return 2 if partial else 0
        except BaseException as exc:
            report['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
            report['error'] = {'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}
            checkpoint()
            write_comparison(folder, report)
            print(f"[실패 단계] {report['stage']} / 결과: {folder / 'summary.json'}")
            raise


def rebuild(args, cfg):
    from v753.analysis_engine import verify_display_artifact, load_bound_source
    from v753.core import capture_files
    from v753.powerpoint import check_office, render
    check_office()
    choices = sorted([p for p in cfg['out_root'].glob('*') if p.is_dir() and (p / 'summary.json').is_file()], reverse=True)
    prior_folder = select_folder(choices, args.run, args.prompt, 'PPT를 다시 만들 V7.5.3 실행')
    old_report = read_json(prior_folder / 'summary.json')
    if old_report.get('version') != VERSION:
        raise V7Error('WRONG_VERSION', 'V7.5.3 결과만 다시 만들 수 있습니다.')
    items = deepcopy(old_report.get('items', []))
    if not items:
        raise V7Error('NO_ANALYZED_ARTICLES', '저장된 분석 결과가 없습니다.')
    for item in items:
        verify_display_artifact(item)
        source, _ = load_bound_source(item)
        caps, notes = capture_files({'source': {**source, 'source_file': item['refreshed_source_file']},
                                    'capture': source.get('capture', {})}, Path(item['refreshed_source_file']).parent)
        if not caps or any('DRM 보호 파일' not in note for note in notes):
            raise V7Error('REBUILD_CAPTURE_INVALID', '저장된 캡처가 없거나 검증에 실패했습니다. 기존 결과를 변경하지 않았습니다.')
        item['captures'] = caps
        item['capture_notes'] = notes
        item['capture_problem'] = False
    with export_lock(cfg['out_root']):
        folder = new_folder(cfg, 'rebuild_')
        report = {'version': VERSION, 'status': 'running', 'stage': 'REBUILD', 'items': items,
                  'rebuild_from': str(prior_folder), 'selected_articles': len(items), 'api_calls': 0,
                  'capture_failures': [], 'missing_capture_articles': [], 'warnings': [],
                  'semantic_review': 'not_reviewed'}
        checkpoint = lambda: write_json(folder / 'summary.json', report)
        checkpoint()
        try:
            render(items, cfg, folder, report, checkpoint)
            partial = bool(report['capture_failures'] or report['warnings'] or report['missing_capture_articles']
                           or any(a['analysis_issues'] or a['metadata_warnings'] for a in items))
            report['status'] = 'partial' if partial else 'completed'
            checkpoint()
            print(f"[PPT 재생성] {report['pptx']} / AI 호출 0 / 원문 재방문 없음")
            return 2 if partial else 0
        except BaseException as exc:
            report.update(status='failed', error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)})
            checkpoint()
            raise


def main():
    parser = argparse.ArgumentParser(description='V7.5.3 새 분석·불만 구분·원문 비교·PPT 출력')
    parser.add_argument('command', choices=('check', 'test', 'export', 'audit-v6', 'rebuild'))
    parser.add_argument('--run')
    parser.add_argument('--count', type=int)
    parser.add_argument('--article', help='테스트할 게시글 ID')
    parser.add_argument('--prompt', action='store_true')
    parser.add_argument('--reanalyze', action='store_true', help='캐시가 있어도 유료 API를 새로 호출')
    args = parser.parse_args()
    print(f'[V{VERSION}] {args.command}')
    try:
        cfg = config(ROOT)
        cfg['project_root'] = ROOT
        if args.command == 'check':
            from v753.analysis_config import load_analysis_config, load_api_key
            from v753.analysis_prompts import make_spec
            from v753.powerpoint import check_office
            from v753.refresh import browser_config
            import openai
            import playwright.sync_api
            print(check_office())
            webcfg = browser_config(ROOT, cfg)
            spec = make_spec(load_analysis_config(ROOT))
            print(f"브라우저: {webcfg['browser']} / 기존 V5 로그인 프로필")
            print(f"모델: {spec['model']} / API 키: {'설정됨' if load_api_key() else '미설정: 기존 02_setup_key_v6.bat 사용'}")
            print(f"카페 표시명 설정: {len(cfg['cafe_names'])}개 / V7.5.2 설정 자동 상속")
            print(f"V6 실행 폴더: {len(run_choices(cfg))}개 / 출력: {cfg['out_root']}")
            print('CHECK_OK: 설정·모듈·PowerPoint 등록 확인. API·브라우저 실제 실행은 하지 않았습니다.')
            return 0
        if args.command == 'audit-v6':
            from v753.comparison import inspect_v6
            folder = select_folder(run_choices(cfg), args.run, args.prompt, '확인할 V6 실행')
            items, errors, _ = load_articles(folder, cfg, ROOT)
            rows = inspect_v6(items)
            with export_lock(cfg['out_root']):
                out = new_folder(cfg, 'audit_')
                write_json(out / 'v6_mapping_audit.json', {'items': rows, 'errors': errors, 'api_calls': 0})
                for row in rows:
                    print(f"{row['article_id']} / 저장 AI 분류: {row['stored_ai_document_type']} / 별도 불만 구분 항목: {row['stored_ai_candidate_has_complaint']} / 기존 PPT: -")
                print(f'[읽기 전용 점검 결과] {out}')
            return 2 if errors else 0
        if args.command == 'rebuild':
            return rebuild(args, cfg)
        return do_export(args, cfg)
    except (KeyboardInterrupt, EOFError):
        print('취소했습니다. 기존 결과는 보존됩니다.')
        return 130
    except Exception as exc:
        print(f"[중단: {getattr(exc, 'code', type(exc).__name__)}] {exc}")
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
