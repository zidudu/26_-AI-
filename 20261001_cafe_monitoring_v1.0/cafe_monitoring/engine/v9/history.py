"""Include V8/alpha/8.1/8.2 and V9 records without modifying any history."""
from v8.history import collection_paths, validated_record
from pathlib import Path
from v8.configuration import V8Error, read_json, write_json
from v9.collection_control import log as print


def scan(root,cafe_id,start,end,legacy_out):
    paths=set(collection_paths(root,legacy_out))
    for mode in ('runs','period'):
        paths.update((root/'output_v9'/mode).glob('*/cafes/*/collect_*/collection.json'))
    prior,overlaps,issues=set(),[],[]
    checked=incomplete=0
    for path in sorted(paths):
        try:
            row=validated_record(path)
            if row is None:
                incomplete+=1
                continue
            data,lo,hi,keys=row
            if str(data['cafe_id'])!=str(cafe_id): continue
            checked+=1
            prior.update(keys)
            if start<hi and lo<end:
                overlaps.append(dict(collection=str(path),start=lo.isoformat(),end=hi.isoformat(),articles=len(keys)))
        except Exception:
            issues.append(str(path))
    return dict(checked_collections=checked,overlapping_periods=overlaps,
                unreadable_collections=issues,incomplete_collections_skipped=incomplete),prior


def announce_history(history):
    count = len(history['overlapping_periods'])
    if count:
        print(f'[기간 중복 알림] 과거 수집 완료 기록 {count}개와 기간이 겹칩니다.', flush=True)
        for old in history['overlapping_periods'][:3]:
            print(f"  {old['start'][:16]} ~ {old['end'][:16]} / 당시 {old['articles']}건", flush=True)
        print('기간이 겹쳐도 다시 수집합니다. 실제 게시글 중복 수는 수집 후 표시합니다.', flush=True)
    else:
        print('[기간 중복 확인] 확인 가능한 과거 수집 완료 기록과 겹치는 기간이 없습니다.', flush=True)
    if history['unreadable_collections']:
        print(f"[중복 확인 제한] 과거 기록 {len(history['unreadable_collections'])}개를 읽거나 검증하지 못했습니다.", flush=True)


def compare_collected(collection, prior_keys, history, output):
    result = validated_record(Path(collection) / 'collection.json')
    if result is None:
        raise ValueError('현재 수집 미완료')
    data, _, _, keys = result
    duplicates = sorted(keys & prior_keys)
    record = {**history, 'status': 'checked', 'selected_articles': len(keys), 'previously_collected_articles': len(duplicates), 'new_to_saved_history': len(keys - prior_keys), 'duplicate_articles': [{'cafe_id': cafe, 'article_id': aid} for cafe, aid in duplicates], 'action': 'recollect_all', 'comparison': 'same_cafe_and_article_id'}
    audit_path = Path(collection) / 'search_audit.json'
    if audit_path.is_file():
        try:
            record['within_run_duplicates_skipped'] = int(read_json(audit_path).get('duplicates', 0))
        except (OSError, ValueError, TypeError, V8Error):
            pass
    write_json(output, record)
    print(f'[게시글 중복 알림] 이번 {len(keys)}건 중 이전 수집 글 {len(duplicates)}건 / 저장 이력에 없는 글 {len(keys - prior_keys)}건', flush=True)
    if record.get('within_run_duplicates_skipped'):
        print(f"[검색 내 중복 정리] 같은 게시글의 반복 검색 결과 {record['within_run_duplicates_skipped']}회를 건너뛰었습니다.", flush=True)
    if duplicates:
        print('중복 게시글도 이번 분석·PPT 대상에 포함합니다. 메일 발송을 선택했다면 다시 포함되어 발송될 수 있습니다.', flush=True)
    print(f'[중복 확인 기록] {output}', flush=True)
    return record
