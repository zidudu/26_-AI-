"""Asynchronous port. Generated at build time; original business rules retained."""
import time
from copy import deepcopy
from v754.collect.metadata import FIELDS, READ_COUNTS, READ_CAFE, now
from v9.collection_control import check_stop

async def observe(page):
    check_stop()
    frame = page.frame(name='cafe_main')
    if frame is None:
        from v754.collect.collector import CollectorError
        raise CollectorError('METADATA_FRAME_MISSING', '게시글 프레임이 없어 숫자를 확인하지 못했습니다.')
    counts = await frame.evaluate(READ_COUNTS)
    outer = await page.evaluate(READ_CAFE)
    inner = await frame.evaluate(READ_CAFE)
    cafe = dict(outer if outer.get('value') else inner)
    cafe['scope'] = 'outer_page' if outer.get('value') else 'article_frame'
    cafe['candidates_by_scope'] = {'outer_page': outer, 'article_frame': inner}
    if outer.get('value') and inner.get('value') and (outer['value'] != inner['value']):
        cafe.update(value=None, status='conflict_between_frames')
    if any(('conflict' in info.get('status', '') for info in (outer, inner))):
        cafe.update(value=None, status='conflict_in_candidates')
    return {'observed_at': now(), **counts, 'cafe_name': cafe}

async def initialize_metadata(page, article):
    initial = await observe(page)
    initial['stage'] = 'initial'
    article['metadata_audit'] = {'version': '7.5.4', 'initial_collector_metadata': deepcopy(article.get('metadata', {})), 'observations': [initial], 'warnings': [], 'selection_policy': 'stable_before_capture'}
    article['metadata'] = {'view_count': None, 'comment_count': None, 'cafe_name': None, 'status': {k: 'not_verified_before_capture' for k in FIELDS}}

async def settle_metadata(page, article, *, timeout=6.0, minimum=2.0, stable_for=1.0, clock=time.monotonic):
    audit = article['metadata_audit']
    start = clock()
    tracks = {k: {'signature': None, 'since': start} for k in FIELDS}
    chosen = None
    while True:
        snapshot = await observe(page)
        snapshot['stage'] = 'before_capture_poll'
        audit['observations'].append(snapshot)
        elapsed = clock() - start
        readiness = {}
        for key in FIELDS:
            info = snapshot[key]
            signature = (info.get('status'), info.get('value'), info.get('selector'))
            if signature != tracks[key]['signature']:
                tracks[key] = {'signature': signature, 'since': clock()}
            readiness[key] = info.get('status') == 'observed' and clock() - tracks[key]['since'] >= stable_for
        chosen = snapshot
        if elapsed >= minimum and all(readiness.values()):
            break
        if elapsed >= timeout:
            break
        await page.wait_for_timeout(min(250, max(1, (timeout - elapsed) * 1000)))
    meta = {'observed_at': chosen['observed_at'], 'status': {}, 'cafe_name': chosen['cafe_name'].get('value')}
    for key in FIELDS:
        info = chosen[key]
        good = readiness[key] and elapsed >= minimum
        meta[key] = info.get('value') if good else None
        meta[key + '_raw'] = info.get('raw')
        meta['status'][key] = 'stable_observed' if good else 'unstable' if info.get('status') == 'observed' else info.get('status', 'missing')
        if not good:
            audit['warnings'].append(key.upper() + '_UNVERIFIED')
    meta['cafe_name_source'] = chosen['cafe_name'].get('scope') if meta['cafe_name'] else 'unverified'
    article['metadata'] = meta
    audit['before_capture'] = deepcopy(chosen)
    audit['stable_window_seconds'] = stable_for
    audit['wait_seconds'] = round(elapsed, 3)
    audit['selected'] = deepcopy(meta)
    return meta

async def check_after_capture(page, article):
    audit = article['metadata_audit']
    after = await observe(page)
    after['stage'] = 'after_capture'
    audit['observations'].append(after)
    audit['after_capture'] = deepcopy(after)
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
