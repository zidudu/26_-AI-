"""Small, dependency-free runtime primitives."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import re

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'engine'


class Problem(Exception):
    def __init__(self, message, status=400, code='INVALID_REQUEST'):
        super().__init__(message)
        self.status, self.code = status, code


def now():
    return datetime.now(KST)


def stamp(value=None):
    return (value or now()).astimezone(KST).isoformat(timespec='seconds')


def parse_time(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (d if d.tzinfo else d.replace(tzinfo=KST)).astimezone(KST)
    except (ValueError, TypeError):
        raise Problem('날짜/시간 형식을 확인하세요.') from None


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(dumps(value), encoding='utf-8')
    temp.replace(path)


def addresses(text):
    result = [s.strip() for s in re.split('[;,\n]', str(text or '')) if s.strip()]
    if len(result) > 100 or any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', s) for s in result):
        raise Problem('메일 주소 형식을 확인하세요. 여러 주소는 세미콜론으로 구분합니다.')
    return result


class InstanceLock:
    """OS-held lock, automatically released on process exit; never deletes a lock."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0)
        if not self.file.read(1):
            self.file.write(b'0'); self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close(); self.file = None
            raise Problem('V10 또는 수집 작업이 이미 실행 중입니다.', 409, 'ALREADY_RUNNING') from None
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.close(); self.file = None
