"""V6 초안을 만들지 않고 새 수집 원문을 분석 입력에 직접 연결합니다."""
from copy import deepcopy
from pathlib import Path
from .core import digest, read_json, write_json, capture_files, V7Error
from .period import Window
from .refresh import count_display


def build_item(source, path, cfg, window):
    meta = deepcopy(source.get('metadata') or {})
    cafe = meta.get('cafe_name')
    if not cafe and cfg['cafe_names'].get(source['cafe_id']):
        cafe = cfg['cafe_names'][source['cafe_id']].strip()
        meta['cafe_name_source'] = 'user_config'
    meta['cafe_name'] = cafe
    images, notes = capture_files({'source': {**source, 'source_file': str(path)},
                                  'capture': source.get('capture', {})}, path.parent)
    warnings = list(source.get('metadata_audit', {}).get('warnings', []))
    if not cafe:
        warnings.append('CAFE_NAME_UNVERIFIED')
    counts = {k: count_display(meta, v) for k, v in [('views', 'view_count'), ('comments', 'comment_count')]}
    warnings += [k.upper() + '_UNVERIFIED' for k, v in counts.items() if v == '미확인']
    identity = digest({k: source[k] for k in ('title', 'body', 'written_at')})
    return {'id': source['article_id'], 'cafe_id': source['cafe_id'],
            'key': source['cafe_id'] + '_' + source['article_id'] + '_' + identity,
            **{k: source[k] for k in ('title', 'url', 'written_at', 'collected_at')},
            'source_kind': 'period_collection', 'refreshed_source_file': str(path),
            'source_artifact_sha256': digest(source), 'source_input_sha256': identity,
            'selection_window': window.record(), 'matched_keywords': [],
            'cafe_name': cafe or '미확인', 'metadata': meta, **counts,
            'metadata_warnings': sorted(set(warnings)), 'metadata_before': {},
            'metadata_observed_at': meta.get('observed_at'),
            'captures': images, 'capture_notes': notes,
            'capture_problem': not images or any('DRM 보호 파일' not in n for n in notes),
            'vehicle': '-', 'specs': '-', 'complaint': '-', 'same_count': '-',
            'display_text': '', 'source_mode': False, 'document_type': 'unclear',
            'review_status': 'not_reviewed', 'review_notes': [],
            'provenance': {'selection': 'written_at_period', 'prior_v6_analysis': False}}


def load_period_source(item):
    source = read_json(item['refreshed_source_file'])
    if (digest(source) != item['source_artifact_sha256']
            or (source['cafe_id'], source['article_id']) != (item['cafe_id'], item['id'])
            or digest({k: source[k] for k in ('title', 'body', 'written_at')}) != item['source_input_sha256']):
        raise V7Error('SOURCE_CHANGED', '기간 수집 이후 원문 파일이나 식별자가 변경됐습니다.')
    if not Window.from_record(item['selection_window']).contains(source['written_at']):
        raise V7Error('OUTSIDE_PERIOD', '저장된 원문 작성 시각이 선정 기간 밖입니다.')
    if any(item[k] != source[k] for k in ('title', 'url', 'written_at')):
        raise V7Error('SOURCE_DISPLAY_MISMATCH', '표시할 제목·URL·작성일이 저장 원문과 다릅니다.')
    return source


def save_collection(folder, report, items):
    data = {'version': '7.5.4', 'kind': 'period_collection', 'window': report['window'],
            'keywords': report['keywords'], 'cafe_id': report['cafe_id'],
            'range_search_complete': report['range_search_complete'],
            'articles_verified_complete': report['articles_verified_complete'],
            'collection_complete': report['collection_complete'], 'items': items}
    data['artifact_sha256'] = digest(data)
    write_json(folder / 'collection.json', data)


def load_collection(folder):
    data = read_json(folder / 'collection.json')
    if (data.get('version') != '7.5.4' or data.get('kind') != 'period_collection'
            or data.get('artifact_sha256') != digest({k: v for k, v in data.items() if k != 'artifact_sha256'})):
        raise V7Error('COLLECTION_CHANGED', '수집 목록의 형식·무결성 검사에 실패했습니다.')
    if not all(data.get(k) is True for k in ('collection_complete', 'range_search_complete', 'articles_verified_complete')):
        raise V7Error('COLLECTION_INCOMPLETE', '기간 수집이 미완료입니다. 수집 진단을 확인하고 해당 기간을 다시 수집하세요.')
    items = deepcopy(data['items'])
    identities = set()
    window = Window.from_record(data['window'])
    for item in items:
        identity = (item['cafe_id'], item['id'])
        if identity in identities or item['cafe_id'] != data['cafe_id'] or item['selection_window'] != window.record():
            raise V7Error('COLLECTION_MISMATCH', '중복 게시글 또는 서로 다른 카페·기간이 섞였습니다.')
        identities.add(identity)
        source = load_period_source(item)
        path = Path(item['refreshed_source_file'])
        caps, notes = capture_files({'source': {**source, 'source_file': str(path)},
                                    'capture': source.get('capture', {})}, path.parent)
        item['captures'], item['capture_notes'] = caps, notes
        item['capture_problem'] = not caps or any('DRM 보호 파일' not in n for n in notes)
    return data, items
