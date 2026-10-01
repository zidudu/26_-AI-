"""선택한 V6 원문을 재수집합니다. AI 호출/기존 V5·V6 결과 변경은 없습니다."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import math

from .core import V7Error, capture_files, digest, read_json, validate_draft, write_json
from .collect.collector import CollectorError, Target, collect_article, read_config, save_article
from .collect.run_lock import RunLock

STOP_CODES = {'LOGIN_REQUIRED', 'AUTH_REQUIRED', 'REQUEST_BLOCKED', 'HTTP_ERROR'}


def browser_config(root, cfg):
    conf, _ = read_config(root / 'config_v5.json')
    conf['profile_lock'] = conf['profile_dir'].parent / (conf['profile_dir'].name + '_v5.lock')
    gap = conf.get('request_interval_seconds', 2)
    if type(gap) not in (int, float) or not math.isfinite(gap) or not 1 <= gap <= 60:
        raise V7Error('CONFIG_ERROR', 'request_interval_seconds는 1~60초입니다.')
    conf['request_interval_seconds'] = gap
    out = cfg['out_root'].resolve()
    for reserved in (conf['profile_dir'], conf['output_dir'], cfg['v6_root']):
        if out == reserved or reserved in out.parents or out in reserved.parents:
            raise V7Error('CONFIG_ERROR', 'output_v754는 기존 수집 결과·로그인 프로필과 분리하세요.')
    return conf


def count_display(meta, key):
    value = meta.get(key)
    status = meta.get('status', {}).get(key)
    return str(value) if type(value) is int and value >= 0 and status == 'stable_observed' else '미확인'


def build_refreshed_item(original, collected, source_path, cfg):
    draft = read_json(original['draft_path'])
    validate_draft(draft)
    if draft['artifact_sha256'] != original['draft_sha256']:
        raise V7Error('DRAFT_CHANGED', '작업 도중 V6 초안이 바뀌었습니다.')
    prior = draft['source']
    if (collected['cafe_id'], collected['article_id']) != (prior['cafe_id'], prior['article_id']):
        raise V7Error('WRONG_ARTICLE', '재수집한 글 식별자가 V6 원문과 다릅니다.')
    actual = digest({k: collected[k] for k in ('title', 'body', 'written_at')})
    if actual != prior['input_sha256']:
        raise V7Error('SOURCE_CHANGED', '제목·본문·작성일이 달라 같은 원문에 대한 비교 분석에서 제외했습니다.')
    meta = deepcopy(collected.get('metadata') or {})
    cafe = meta.get('cafe_name')
    if not cafe and cfg['cafe_names'].get(prior['cafe_id']):
        cafe = cfg['cafe_names'][prior['cafe_id']].strip()
        meta['cafe_name_source'] = 'user_config'
    meta['cafe_name'] = cafe
    current = deepcopy(original)
    images, notes = capture_files({'source': {**collected, 'source_file': str(source_path)},
                                   'capture': collected.get('capture', {})}, source_path.parent)
    current.update(cafe_name=cafe or '미확인', views=count_display(meta, 'view_count'),
        comments=count_display(meta, 'comment_count'), collected_at=collected['collected_at'],
        captures=images, capture_notes=notes,
        capture_problem=not images or any('DRM 보호 파일' not in n for n in notes),
        metadata=meta, metadata_observed_at=meta.get('observed_at'),
        metadata_before=deepcopy(prior.get('metadata', {})),
        refreshed_source_file=str(source_path), source_input_matched=True,
        metadata_warnings=list(collected.get('metadata_audit', {}).get('warnings', [])))
    if not cafe:
        current['metadata_warnings'].append('CAFE_NAME_UNVERIFIED')
    for key in ('views', 'comments'):
        if current[key] == '미확인':
            current['metadata_warnings'].append(key.upper() + '_UNVERIFIED')
    current['metadata_warnings'] = sorted(set(current['metadata_warnings']))
    current['review_notes'] += [
        f"V7.5.4 재수집: {collected['collected_at']}",
        f"댓글·조회수 확인 시각: {meta.get('observed_at') or '미확인'}",
        f"카페 이름 근거: {meta.get('cafe_name_source', 'unverified')}",
        '기존 V6 제목·본문·작성일 일치 확인. 다음 단계에서 새 분석 입력으로 사용합니다.',
    ]
    current['review_notes'] += current['metadata_warnings']
    return current


def refresh_articles(selected, cfg, folder, report, checkpoint, *, page, timeout_error, webcfg):
    report['stage'] = 'REFRESH_ARTICLES'
    report['refresh_results'] = []
    report['items'] = []
    report['selected_source_articles'] = [{k: a[k] for k in ('id', 'cafe_id', 'draft_path', 'draft_sha256')}
                                           for a in selected]
    capture_folder = folder / 'source'
    capture_folder.mkdir(exist_ok=False)
    audits = {'version': '7.5.4', 'api_calls': 0, 'items': [],
              'note': '재방문 시점의 값과 새 캡처입니다. 과거 값의 복원 결과가 아닙니다.'}
    def save():
        write_json(folder / 'metadata_audit.json', audits)
        report['processed_articles'] = len(report['refresh_results'])
        report['unprocessed_articles'] = len(selected) - len(report['refresh_results'])
        checkpoint()
    save()
    for number, old in enumerate(selected, 1):
        print(f"[재수집 {number}/{len(selected)}] {old['id']}", flush=True)
        result = {'article_id': old['id'], 'cafe_id': old['cafe_id']}
        try:
            if old['cafe_id'] != webcfg['cafe_id']:
                raise V7Error('CAFE_MISMATCH', 'V5 설정과 다른 카페입니다. 다중 카페는 이후 단계입니다.')
            new = collect_article(page, Target(old['cafe_id'], old['id']),
                                  {**webcfg, 'capture_output_dir': capture_folder}, timeout_error)
            path = save_article(capture_folder, new)
            evidence = {'article_id': old['id'], 'cafe_id': old['cafe_id'], 'source_file': str(path),
                        'saved_v6': {'views': old['views'], 'comments': old['comments']},
                        'trace': new.get('metadata_audit', {})}
            audits['items'].append(evidence)
            result['source_file'] = str(path)
            item = build_refreshed_item(old, new, path, cfg)
            # 선택 결과를 별도로 남겨 사용자 설정 카페 이름도 출처를 구분합니다.
            evidence['ppt_metadata'] = item['metadata']
            result.update(status='refreshed', views=item['views'], comments=item['comments'],
                cafe_name=item['cafe_name'], metadata_warnings=item['metadata_warnings'],
                capture_files=len(item['captures']), capture_problem=item['capture_problem'])
            report['items'].append(item)
            initial = (new.get('metadata_audit', {}).get('observations') or [{}])[0]
            before = initial.get('comment_count', {}).get('value')
            print(f"  댓글: V6 저장 {old['comments']} / 최초 관측 {before} / 캡처 전 검증 {item['comments']}")
            print(f"  조회수 {item['views']} / 매체 {item['cafe_name']} / 새 캡처 {len(item['captures'])}장")
            if item['metadata_warnings']:
                print('  [확인 필요] ' + ', '.join(item['metadata_warnings']))
        except (CollectorError, V7Error) as exc:
            result.update(status='excluded', code=exc.code, message=str(exc))
            print(f"  [제외] {exc.code} / {exc}")
            if exc.code in STOP_CODES:
                report['stop_code'] = exc.code
        report['refresh_results'].append(result)
        save()
        if report.get('stop_code'):
            break
        if number < len(selected):
            page.wait_for_timeout(webcfg['request_interval_seconds'] * 1000)
    return report['items']


def refresh_with_browser(selected, cfg, folder, report, checkpoint, root):
    webcfg = browser_config(root, cfg)
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    with ExitStack() as stack:
        # V5と同じプロファイルロックを使用し、並行操作を防止します。
        stack.enter_context(RunLock(webcfg['profile_lock']))
        pw = stack.enter_context(sync_playwright())
        options = {'user_data_dir': str(webcfg['profile_dir']), 'headless': False,
                   'locale': 'ko-KR', 'timezone_id': 'Asia/Seoul',
                   'viewport': {'width': 1365, 'height': 900}}
        if webcfg['browser'] != 'chromium':
            options['channel'] = webcfg['browser']
        context = pw.chromium.launch_persistent_context(**options)
        stack.callback(context.close)
        return refresh_articles(selected, cfg, folder, report, checkpoint,
            page=context.new_page(), timeout_error=PWTimeout, webcfg=webcfg)
