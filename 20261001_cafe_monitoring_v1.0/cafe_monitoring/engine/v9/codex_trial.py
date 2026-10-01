"""One saved article -> actual Codex -> V9 validation; never collects or mails."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import argparse
import sys
import traceback
import uuid

from v754.core import V7Error, read_json, write_json
from v8.analysis_engine import load_bound_source, verify_display_artifact
from v9.ai_provider import load_settings, analysis_binding, show_settings, ProviderError
from v9.analysis_engine import analyze_articles
from v9.summary_repair import SummaryRepair
from v9.comparison import write_comparison


def saved_choices(root):
    paths = []
    for mode in ('period', 'runs'):
        paths.extend((Path(root) / 'output_v9' / mode).glob('*/export_*/summary.json'))
    choices = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = read_json(path)
        except V7Error:
            continue
        items = data.get('items', [])
        if (data.get('collection_complete') is True and isinstance(items, list)
                and any(isinstance(i, dict) and i.get('analysis_result_file') for i in items)):
            choices.append(path)
            if len(choices) == 20:
                break
    return choices


def select_item(root, summary_path, article_key=None):
    root, path = Path(root).resolve(), Path(summary_path).resolve()
    if not path.is_relative_to(root / 'output_v9') or path.name != 'summary.json':
        raise ProviderError('TRIAL_SOURCE_PATH', '기존 output_v9 실행의 summary.json을 지정하세요.')
    data = read_json(path)
    if data.get('collection_complete') is not True or not isinstance(data.get('items'), list):
        raise ProviderError('TRIAL_SOURCE_INVALID', '완료 확인된 수집 및 저장 분석이 필요합니다.')
    rows = [i for i in data['items'] if isinstance(i, dict) and i.get('analysis_result_file')]
    if article_key:
        rows = [i for i in rows if f"{i.get('cafe_id')}:{i.get('id')}" == article_key]
    if not rows:
        raise ProviderError('TRIAL_SOURCE_MISSING', '선택한 저장 분석 게시글을 찾지 못했습니다.')
    item = deepcopy(rows[0])
    verify_display_artifact(item)
    load_bound_source(item)
    # Every new analysis writes its own artifact; no prior display/repair state leaks.
    for key in list(item):
        if key.startswith('analysis_') or key in ('prior_analysis_candidate', 'summary_repair', 'summary_pending'):
            item.pop(key)
    return item


def run_trial(root, summary_path, *, article_key=None, repair=False):
    root = Path(root).resolve()
    settings = load_settings(root)
    if settings['provider'] != 'codex':
        raise ProviderError('CODEX_NOT_SELECTED', '44_ai_provider_v95.bat에서 1: Codex를 선택하세요.')
    item = select_item(root, summary_path, article_key)
    folder = root / 'output_v95_trial' / ('trial_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True, exist_ok=False)
    report = {'application_version': '9.5.1-codex', 'status': 'running',
              'source_summary': str(Path(summary_path).resolve()),
              'article_key': f"{item['cafe_id']}:{item['id']}",
              'mail_sent': False, 'collection_performed': False, 'ppt_generated': False,
              'repair_requested': repair, 'ai_settings': settings}
    checkpoint = lambda: write_json(folder / 'summary.json', report)
    checkpoint()
    print(f"[Codex 실제 시험] {report['article_key']} / 최초 분석 1회" + (' + 재요약 최대 1회' if repair else ''))
    show_settings(settings, requested_concurrency=1)
    print('[결과 폴더]', folder)
    try:
        with analysis_binding(root, settings, folder) as (spec, factory):
            items = analyze_articles([item], spec, folder, report, checkpoint, concurrency=1,
                                     analyzer_factory=factory, provider='codex')
            repaired = False
            if items and repair:
                repaired = SummaryRepair(spec, folder, report, checkpoint,
                    analyzer_factory=factory, provider='codex')(items[0], 'MANUAL_CODEX_TRIAL')
            for analyzed in items:
                verify_display_artifact(analyzed)
            report['items'] = items
            valid = (len(items) == 1 and bool(items[0].get('analysis_candidate'))
                     and not items[0].get('summary_pending') and not items[0].get('analysis_issues'))
            if repair:
                valid = valid and repaired
            report['status'] = 'completed' if valid else 'partial'
            checkpoint()
            write_comparison(folder, report)
        print('[Codex 검증 결과]', report['status'])
        print(f"[호출] Codex {report['codex_calls']}회 / OpenAI API {report['api_calls']}회")
        print('[원문·요약 비교]', folder / 'comparison.html')
        print('[수집·PPT·메일] 실행하지 않음 / 기존 결과 보존')
        return (0 if valid else 2), folder
    except BaseException as exc:
        report.update(status='cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)})
        checkpoint()
        (folder / 'error_traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='저장된 게시글 1건을 실제 Codex로 분석·검증합니다')
    parser.add_argument('--summary', type=Path)
    parser.add_argument('--article-key', help='카페ID:게시글ID')
    parser.add_argument('--repair', action='store_true', help='같은 실행기로 재요약 1회도 시험')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    from v754.collect.run_lock import RunLock
    try:
        with RunLock(root / 'output_v8/v8.lock'):
            # Same installed-engine integrity gate as a full V9 run; no browser/model call.
            from v9.backend import Backend
            Backend(root)
            summary = args.summary
            if summary is None:
                choices = saved_choices(root)
                if not choices:
                    raise ProviderError('NO_SAVED_ANALYSIS', 'output_v9에 저장된 분석이 없습니다.')
                for n, path in enumerate(choices, 1):
                    print(f'{n}. {path.parent.parent.name} / {path.parent.name}')
                answer = input('시험할 실행 번호 [1]: ').strip() or '1'
                if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
                    raise ProviderError('INVALID_SELECTION', '표시된 번호를 선택하세요.')
                summary = choices[int(answer) - 1]
            code, _ = run_trial(root, summary, article_key=args.article_key, repair=args.repair)
            return code
    except (KeyboardInterrupt, EOFError):
        print('[취소] 저장된 결과를 확인하세요.')
        return 130
    except Exception as exc:
        print('[시험 중단]', getattr(exc, 'code', type(exc).__name__), str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
