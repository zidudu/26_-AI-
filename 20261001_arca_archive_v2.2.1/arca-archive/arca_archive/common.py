"""공통 유틸: 시간, 해시, 원자적 파일 저장, 오류 타입, 프로세스 잠금.

오류 메시지에는 쿠키·세션 값·요청 헤더·원문 본문을 넣지 않습니다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    """UTC ISO-8601 (초 단위). DB에 저장하는 모든 시각은 이 형식을 사용합니다."""
    return utcnow().replace(microsecond=0).isoformat()


def iso_after(seconds: float) -> str:
    return (utcnow() + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_kst_text(value: str | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    parsed = parse_iso(value)
    return parsed.astimezone(KST).strftime(fmt) if parsed else ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(data)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".partial_", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


def safe_filename(value: str, max_len: int = 80) -> str:
    cleaned = _SAFE_NAME.sub("_", value).strip("._")
    return (cleaned or "file")[:max_len]


class AppError(Exception):
    """코드가 있는 애플리케이션 오류.

    stop=True   : 실행(run) 전체를 중단해야 하는 세션 수준 오류(로그인 필요, 차단 등)
    retryable   : 항목 단위 재시도 대상인지
    """

    def __init__(self, code: str, message: str, *, stop: bool = False, retryable: bool = True,
                 details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.stop = stop
        self.retryable = retryable
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class RunLock:
    """OS 파일 잠금 기반 단일 실행 보장. 강제 종료 후에도 재실행 가능합니다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.file = None

    def acquire(self) -> bool:
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
            else:  # pragma: no cover - Windows 우선
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            return False
        return True

    def release(self) -> None:
        if not self.file:
            return
        try:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None

    def __enter__(self):
        if not self.acquire():
            raise AppError("ALREADY_RUNNING", "같은 데이터 폴더를 사용하는 수집 작업이 이미 실행 중입니다.",
                           stop=True, retryable=False)
        return self

    def __exit__(self, *exc):
        self.release()
