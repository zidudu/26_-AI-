"""Usage: archive_tools.py audit|backup|verify|restore --output PATH [--bundle PATH]."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arca_archive.config import load_settings
from arca_archive.db import Database
from arca_archive.archive_bundle import audit_files, create_bundle, verify_bundle, restore_bundle

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['audit','backup','verify','restore'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    if args.command in ('audit','backup'):
        settings = load_settings()
        if args.command == 'audit':
            import sqlite3
            from types import SimpleNamespace
            conn = sqlite3.connect(f'file:{settings.db_path.as_posix()}?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row
            db = SimpleNamespace(connect=lambda:conn, close=conn.close)
        else:
            db = Database(settings.db_path)
        try:
            result = audit_files(db) if args.command == 'audit' else create_bundle(settings, db, args.output)
        finally:
            db.close()
    else:
        if not args.bundle:
            parser.error('--bundle is required')
        result = verify_bundle(args.bundle) if args.command == 'verify' else restore_bundle(args.bundle, args.output)
    if args.command in ('audit','verify'):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, ensure_ascii=False))
    return 1 if result.get('ok') is False else 0

if __name__ == '__main__':
    raise SystemExit(main())
