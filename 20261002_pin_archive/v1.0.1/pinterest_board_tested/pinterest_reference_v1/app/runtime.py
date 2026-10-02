"""Discover the current local server without trusting a stale port number."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = re.compile(r'^[a-f0-9]{32}$')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def data_root(value: Path | None = None) -> Path:
    return Path(value or os.getenv('PINARCHIVE_DATA_DIR') or ROOT / 'data').resolve()


def runtime_path(value: Path | None = None) -> Path:
    return data_root(value) / 'runtime.json'


def publish_runtime(data_dir: Path, port: int, instance_id: str) -> None:
    path = runtime_path(data_dir)
    record = {'host': '127.0.0.1', 'port': port,
              'base_url': f'http://127.0.0.1:{port}', 'pid': os.getpid(),
              'instance_id': instance_id}
    temporary = path.with_name(f'.runtime-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(record), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def clear_runtime(data_dir: Path, instance_id: str) -> None:
    path = runtime_path(data_dir)
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('instance_id') == instance_id:
            path.unlink(missing_ok=True)
    except (FileNotFoundError, ValueError, OSError, AttributeError):
        pass


def discover_server(data_dir: Path | None = None) -> str:
    path = runtime_path(data_dir)
    try:
        if path.stat().st_size > 4096:
            raise ValueError('Runtime state is oversized.')
        record = json.loads(path.read_text(encoding='utf-8'))
        port = record['port']
        instance_id = record['instance_id']
        if (record.get('host') != '127.0.0.1' or type(port) is not int
                or not 1024 <= port <= 65535 or not isinstance(instance_id, str)
                or not INSTANCE.fullmatch(instance_id)
                or record.get('base_url') != f'http://127.0.0.1:{port}'):
            raise ValueError('Runtime state has invalid fields.')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            health = json.loads(response.read(4097))
        if health.get('app') != 'pinarchive' or health.get('instance_id') != instance_id:
            raise ValueError('Runtime state points to a different server.')
        return record['base_url']
    except (FileNotFoundError, KeyError, TypeError, ValueError, OSError, UnicodeError) as exc:
        raise RuntimeError(f'Pin Archive server is not running at {path}. Start 02_start.bat or provide an explicit local URL.') from exc
