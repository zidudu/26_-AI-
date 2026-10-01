"""작은 공통 함수: 원자적 저장, 해시, 프로세스 잠금, 안전한 오류 메시지."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


class V6Error(Exception):
    def __init__(self, code, message, *, stop=False, uncertain=False):
        super().__init__(message)
        self.code = code
        self.stop = stop
        self.uncertain = uncertain


def now():
    return datetime.now(KST).isoformat()


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".partial_", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def append_event(path, event, **values):
    # API 키, HTTP 헤더, 서버 오류 전문, 게시글 본문은 로그에 넣지 않습니다.
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now(), "event": event, **values}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class RunLock:
    """파일의 존재가 아니라 OS 잠금을 사용하므로 강제 종료 후에도 재실행 가능합니다."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise V6Error("ALREADY_RUNNING", "같은 결과 폴더를 사용하는 V6가 실행 중입니다.") from None
        return self

    def __exit__(self, *args):
        if self.file:
            try:
                self.file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None
