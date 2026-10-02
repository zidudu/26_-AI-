"""별도 프로세스로 수집/인덱싱/미리보기 작업을 실행합니다."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from .library import Store, atomic_text
from .engine import emit


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result_path = Path(sys.argv[2])
    store = Store(request['data_dir'])
    payload = request['payload']
    try:
        if request['kind'] == 'extract':
            from .engine import extract_batch
            result = extract_batch(payload, store)
            total = result['success'] + result['partial'] + result['failed']
            status = 'success' if total and result['success'] == total and not result['errors'] else 'partial' if result['success'] + result['partial'] else 'failed'
        elif request['kind'] == 'import':
            result = store.scan(Path(payload['path']), payload.get('tags', []), lambda msg: emit('stage', stage=msg))
            status = 'partial' if result['errors'] else 'success'
        elif request['kind'] == 'rescan':
            results = []
            for root in payload['roots']:
                emit('stage', stage='폴더 다시 읽는 중: ' + root)
                try:
                    results.append(store.scan(Path(root)))
                except Exception as exc:
                    results.append({'count': 0, 'errors': [str(exc)]})
            result = {'count': sum(r['count'] for r in results), 'roots': results}
            status = 'partial' if any(r.get('errors') for r in results) else 'success'
        elif request['kind'] in {'mobile_preview', 'mobile_batch'}:
            from .performance import make_mobile_preview
            ids = payload.get('item_ids') or [payload['item_id']]
            done, errors = [], []
            for ident in ids:
                try:
                    done.append(make_mobile_preview(ident, store, payload.get('profile', 'mobile720')))
                    emit('library_changed')
                except Exception as exc:
                    errors.append({'item_id': ident, 'error': str(exc)[-2000:]})
                    print('[변환 실패] ' + str(exc), flush=True)
            result = {'count': len(done), 'items': done, 'errors': errors}
            status = 'partial' if done and errors else 'success' if done else 'failed'
        elif request['kind'] == 'preview':
            from .engine import make_preview
            result = make_preview(payload['item_id'], store)
            status = 'success'
        else:
            raise ValueError('알 수 없는 작업 종류')
        atomic_text(result_path, json.dumps({'status': status, 'result': result}, ensure_ascii=False))
        emit('stage', stage={'success': '완료', 'partial': '부분 완료 · 오류 확인', 'failed': '실패 · 오류 확인'}[status], progress=100)
        return 0 if status in {'success', 'partial'} else 1
    except Exception as exc:
        import traceback
        traceback.print_exc()
        atomic_text(result_path, json.dumps({'status': 'failed', 'result': {'error': str(exc)[-5000:]}}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
