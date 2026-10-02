"""File integrity, portable bundles, and isolated restore. No source data is deleted."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from .common import RunLock, AppError, now_iso

def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

def audit_files(db, verify_hash=True):
    faults, checked, size = [], 0, 0
    cache = {}
    for row in db.connect().execute("SELECT id,file_path,file_size,sha256 FROM media WHERE state='downloaded'"):
        checked += 1
        path = Path(row['file_path']) if row['file_path'] else None
        if not path or not path.is_file():
            faults.append({'media_id': row['id'], 'code': 'FILE_MISSING'})
            continue
        actual = path.stat().st_size
        size += actual
        if actual != row['file_size']:
            faults.append({'media_id': row['id'], 'code': 'SIZE_MISMATCH'})
        elif verify_hash:
            if path not in cache:
                cache[path] = file_hash(path)
            if cache[path] != row['sha256']:
                faults.append({'media_id': row['id'], 'code': 'HASH_MISMATCH'})
    integrity = db.connect().execute('PRAGMA quick_check').fetchone()[0]
    return {'at': now_iso(), 'checked': checked, 'bytes': size, 'hash_checked': verify_hash,
            'db_quick_check': integrity, 'faults': faults, 'ok': not faults and integrity == 'ok'}

def _inside(root, name):
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError('Bundle path escapes destination')
    return target

def create_bundle(settings, db, destination, sample_ids=None):
    lock = RunLock(settings.lock_path)
    if not lock.acquire():
        raise AppError('ALREADY_RUNNING', '수집을 중지한 뒤 백업하세요.')
    destination = Path(destination).resolve()
    try:
        destination.mkdir(parents=True, exist_ok=False)
        snapshot = db.backup_to(destination / 'data' / 'arca.sqlite3')
        manifest = {'format': 1, 'created_at': now_iso(), 'scope': 'sample' if sample_ids is not None else 'full',
                    'source_data_path': str(settings.data_path.resolve()), 'files': []}
        config = settings.model_dump()
        config.update(data_dir='data', scheduler_enabled=False, seed_channels=[])
        config['browser']['profile_dir'] = 'data/browser_profile'
        config_path = destination / 'config' / 'settings.json'
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        paths = set()
        conn = sqlite3.connect(snapshot)
        try:
            for mid, path in conn.execute("SELECT id,file_path FROM media WHERE state='downloaded'"):
                if path and (sample_ids is None or mid in sample_ids):
                    paths.add(Path(path))
            if sample_ids is None:
                paths.update(Path(r[0]) for r in conn.execute('SELECT raw_html_path FROM articles WHERE raw_html_path IS NOT NULL'))
                paths.update(p for p in settings.raw_path.rglob('*') if p.is_file())
        finally:
            conn.close()
        required = sum(p.stat().st_size for p in paths)
        if shutil.disk_usage(destination).free < required + 512*1024*1024:
            raise AppError('DISK_SPACE', '백업 공간이 부족합니다.')
        for path in sorted(paths):
            relative = Path('data') / path.resolve().relative_to(settings.data_path.resolve())
            target = _inside(destination, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                manifest['files'].append({'path': path.relative_to(destination).as_posix(), 'bytes': path.stat().st_size, 'sha256': file_hash(path)})
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return manifest
    finally:
        lock.release()

def verify_bundle(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    faults = []
    for item in manifest['files']:
        path = _inside(folder, item['path'])
        if not path.is_file() or path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            faults.append(item['path'])
    return {'ok': not faults, 'scope': manifest['scope'], 'files': len(manifest['files']), 'faults': faults}

def restore_bundle(folder, destination):
    folder, destination = Path(folder).resolve(), Path(destination).resolve()
    verification = verify_bundle(folder)
    if not verification['ok']:
        raise ValueError('Bundle verification failed')
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    destination.mkdir(parents=True, exist_ok=False)
    for item in manifest['files']:
        target = _inside(destination, item['path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_inside(folder, item['path']), target)
    source_root = Path(manifest['source_data_path'])
    conn = sqlite3.connect(destination/'data'/'arca.sqlite3')
    try:
        for table, field in [('media','file_path'), ('articles','raw_html_path')]:
            for row_id, old in conn.execute(f'SELECT id,{field} FROM {table} WHERE {field} IS NOT NULL').fetchall():
                new = _inside(destination/'data', Path(old).relative_to(source_root))
                conn.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (str(new), row_id))
        conn.execute('DELETE FROM queued_runs')
        conn.execute("UPDATE runs SET status='failed',code='RESTORED_SNAPSHOT' WHERE status='running'")
        conn.commit()
    finally:
        conn.close()
    return {**verification, 'destination': str(destination), 'scheduler_enabled': False}
