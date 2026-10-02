"""Bounded live Arca backlog verification; requires the service to be idle/offline."""
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arca_archive.common import RunLock, now_iso
from arca_archive.config import load_settings
from arca_archive.db import Database
from arca_archive.pipeline.runner import run_channel
from arca_archive.archive_bundle import audit_files, create_bundle, restore_bundle, file_hash
from arca_archive.backlog import inspect_backlog
from arca_archive.cli import setup_logging

settings = load_settings()
folder = settings.data_path/'diagnostics'/'v2-acceptance'
folder.mkdir(parents=True, exist_ok=True)
def save(name, value):
    (folder/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
lock = RunLock(settings.lock_path)
if not lock.acquire():
    raise SystemExit('Crawler is active; stop first.')
try:
    # Back up before the v5 schema migration.
    if not (folder/'before.sqlite3').exists():
        source = sqlite3.connect(f'file:{settings.db_path.as_posix()}?mode=ro', uri=True)
        target = sqlite3.connect(folder/'before.sqlite3')
        source.backup(target)
        target.close()
        source.close()
    db = Database(settings.db_path)
    interrupted = db.active_runs()
    save('interrupted_runs.json', interrupted)
    db.abort_stale_runs('서버 프로세스 부재를 확인한 뒤 진단에서 중단 기록을 정리했습니다. 종료 원인은 미확인입니다.')
    for run in interrupted:
        if run.get('channel_id') and db.get_channel(run['channel_id'])['enabled']:
            db.queue_run(run['channel_id'], 'resume')
    cohort = {r['id']: r['state'] for r in db.connect().execute("SELECT id,state FROM media WHERE state IN ('pending','failed','expired','error')")}
    save('cohort_before.json', {'at': now_iso(), 'media': cohort, 'backlog': inspect_backlog(db, limit=0)})
finally:
    lock.release()
settings.media.backlog_time_budget_minutes = 1
settings.media.backlog_files_per_article = 2
settings.crawl.max_rechecks_per_run = 3
setup_logging(settings, console=False)
results = []
for ch in db.list_channels():
    if ch['enabled'] and ch['site'] == 'arca':
        result = run_channel(settings, db, ch['id'], trigger='backlog_manual')
        results.append(result)
        save('live_runs.json', results)
        print(json.dumps({'channel': ch['slug'], 'run': result['id'], 'status': result['status'], 'code': result['code'], 'stats': result['stats']}, ensure_ascii=False), flush=True)
after = {r['id']: r['state'] for r in db.connect().execute('SELECT id,state FROM media')}
recovered = [mid for mid in cohort if after.get(mid) == 'downloaded']
save('cohort_after.json', {'at':now_iso(), 'initial_unfinished':len(cohort), 'recovered_ids':recovered,
                         'remaining_states':{state:sum(after.get(mid)==state for mid in cohort) for state in set(after.values())},
                         'backlog':inspect_backlog(db, limit=0)})
audit = audit_files(db)
save('file_integrity.json', audit)
print(json.dumps({'integrity_checked':audit['checked'],'integrity_ok':audit['ok'],'faults':len(audit['faults']),'recovered':len(recovered)}, ensure_ascii=False), flush=True)
sample = set(recovered[:5])
sample.update(r[0] for r in db.connect().execute("SELECT id FROM media WHERE state='downloaded' AND file_size<1000000 ORDER BY id DESC LIMIT 5"))
bundle = folder/'sample-bundle'
if not bundle.exists():
    create_bundle(settings, db, bundle, sample_ids=sample)
restored = folder/'isolated-restore'
if not restored.exists():
    restore = restore_bundle(bundle, restored)
    restored_db = Database(restored/'data'/'arca.sqlite3')
    from fastapi.testclient import TestClient
    from arca_archive.web.app import create_app
    restored_settings = load_settings(restored/'config'/'settings.json')
    responses = []
    with TestClient(create_app(restored_settings, restored_db)) as client:
        for mid in sample:
            m = restored_db.get_media(mid)
            response = client.get(f'/media/{mid}/file', headers={'Range':'bytes=0-31'})
            responses.append({'media_id':mid,'status':response.status_code,'hash_ok':file_hash(m['file_path'])==m['sha256'],
                              'local_restore_path':str(Path(m['file_path']).resolve()).startswith(str(restored.resolve()))})
        routes = {path:client.get(path).status_code for path in ['/','/backlog','/articles','/media','/errors']}
    restored_db.close()
    save('restore_verification.json', {'restore':restore,'sample_files':responses,'routes':routes})
    print(json.dumps({'restore_sample_files':len(responses),'local_files_ok':all(r['status']==206 and r['hash_ok'] and r['local_restore_path'] for r in responses),'routes':routes}), flush=True)
db.close()
