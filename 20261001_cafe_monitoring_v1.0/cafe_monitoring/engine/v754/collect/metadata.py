"""댓글/조회수의 관측 시각·선택자·변화를 기록하고 캡처 직전 값을 채택합니다."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time

FIELDS = ('view_count', 'comment_count')
READ_COUNTS = Path(__file__).with_name('read_counts.js').read_text(encoding='utf-8')
READ_CAFE = Path(__file__).with_name('read_cafe.js').read_text(encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def observe(page):
    frame = page.frame(name='cafe_main')
    if frame is None:
        from .collector import CollectorError
        raise CollectorError('METADATA_FRAME_MISSING', '게시글 프레임이 없어 숫자를 확인하지 못했습니다.')
    counts = frame.evaluate(READ_COUNTS)
    outer = page.evaluate(READ_CAFE)
    inner = frame.evaluate(READ_CAFE)
    cafe = dict(outer if outer.get('value') else inner)
    cafe['scope'] = 'outer_page' if outer.get('value') else 'article_frame'
    cafe['candidates_by_scope'] = {'outer_page': outer, 'article_frame': inner}
    if outer.get('value') and inner.get('value') and outer['value'] != inner['value']:
        cafe.update(value=None, status='conflict_between_frames')
    if any('conflict' in info.get('status', '') for info in (outer, inner)):
        cafe.update(value=None, status='conflict_in_candidates')
    return {'observed_at': now(), **counts, 'cafe_name': cafe}


def initialize_metadata(page, article):
    initial = observe(page)
    initial['stage'] = 'initial'
    article['metadata_audit'] = {'version': '7.5.4', 'initial_collector_metadata': deepcopy(article.get('metadata', {})),
        'observations': [initial], 'warnings': [], 'selection_policy': 'stable_before_capture'}
    # 최초 관측은 근거로만 보관합니다. 캡처 전 검증이 실패하면 기존 0으로 되돌아가지 않습니다.
    article['metadata'] = {'view_count': None, 'comment_count': None, 'cafe_name': None,
        'status': {k: 'not_verified_before_capture' for k in FIELDS}}


def settle_metadata(page, article, *, timeout=6.0, minimum=2.0, stable_for=1.0,
                    clock=time.monotonic):
    audit = article['metadata_audit']
    start = clock()
    tracks = {k: {'signature': None, 'since': start} for k in FIELDS}
    chosen = None
    while True:
        snapshot = observe(page)
        snapshot['stage'] = 'before_capture_poll'
        audit['observations'].append(snapshot)
        elapsed = clock() - start
        readiness = {}
        for key in FIELDS:
            info = snapshot[key]
            signature = (info.get('status'), info.get('value'), info.get('selector'))
            if signature != tracks[key]['signature']:
                tracks[key] = {'signature': signature, 'since': clock()}
            readiness[key] = (info.get('status') == 'observed' and
                              clock() - tracks[key]['since'] >= stable_for)
        chosen = snapshot
        if elapsed >= minimum and all(readiness.values()):
            break
        if elapsed >= timeout:
            break
        page.wait_for_timeout(min(250, max(1, (timeout - elapsed) * 1000)))
    meta = {'observed_at': chosen['observed_at'], 'status': {}, 'cafe_name': chosen['cafe_name'].get('value')}
    for key in FIELDS:
        info = chosen[key]
        good = readiness[key] and elapsed >= minimum
        meta[key] = info.get('value') if good else None
        meta[key + '_raw'] = info.get('raw')
        meta['status'][key] = 'stable_observed' if good else (
            'unstable' if info.get('status') == 'observed' else info.get('status', 'missing'))
        if not good:
            audit['warnings'].append(key.upper() + '_UNVERIFIED')
    meta['cafe_name_source'] = chosen['cafe_name'].get('scope') if meta['cafe_name'] else 'unverified'
    article['metadata'] = meta
    audit['before_capture'] = deepcopy(chosen)
    audit['stable_window_seconds'] = stable_for
    audit['wait_seconds'] = round(elapsed, 3)
    audit['selected'] = deepcopy(meta)
    return meta


def check_after_capture(page, article):
    audit = article['metadata_audit']
    after = observe(page)
    after['stage'] = 'after_capture'
    audit['observations'].append(after)
    audit['after_capture'] = deepcopy(after)
    # 변한 값을 캡처와 같은 시점의 확정값으로 표시하지 않습니다. 재실행으로 다시 관측합니다.
    for key in FIELDS:
        before = article['metadata'].get(key)
        if before is not None and (after[key].get('status') != 'observed' or after[key].get('value') != before):
            audit['warnings'].append(key.upper() + '_CHANGED_DURING_CAPTURE')
            article['metadata'][key] = None
            article['metadata']['status'][key] = 'changed_during_capture'
    before_cafe = article['metadata'].get('cafe_name')
    if before_cafe and after['cafe_name'].get('value') != before_cafe:
        article['metadata']['cafe_name'] = None
        article['metadata']['cafe_name_source'] = 'unverified'
        audit['warnings'].append('CAFE_NAME_CHANGED_DURING_CAPTURE')
    audit['selected'] = deepcopy(article['metadata'])
