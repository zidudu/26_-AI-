"""미디어 다운로드 공통 구현(httpx 스트리밍).

- 청크 단위로 받으며 중지 이벤트를 확인하므로 큰 파일 도중에도 수 초 안에 멈춥니다.
- CDN(ac.arca.live 등)은 서명 URL만 검사하므로 브라우저 세션이 없어도 받을 수 있습니다.
  필요하면 호출자가 쿠키를 넘겨 함께 보냅니다.
"""
from __future__ import annotations

import threading
import time
import json
import re
import hashlib
from pathlib import Path

import httpx

from ..common import AppError
from .base import DownloadResult


def stream_download(client: httpx.Client, url: str, referer: str, max_bytes: int,
                    stop_event: threading.Event | None = None, cookies: dict[str, str] | None = None,
                    max_duration_seconds: float | None = None, resume_path: Path | None = None) -> DownloadResult:
    # 바이트 범위는 압축 전송과 섞지 않습니다. If-Range로 같은 파일인지 확인한 뒤 이어받습니다.
    headers = {"Referer": referer, "Accept": "*/*", "Accept-Encoding": "identity"}
    metadata_path = resume_path.with_suffix(".json") if resume_path else None
    saved, offset = {}, 0
    if resume_path and resume_path.exists() and metadata_path.exists():
        try:
            saved = json.loads(metadata_path.read_text(encoding="utf-8"))
            offset = resume_path.stat().st_size
            if not saved.get("validator") or not 0 < offset < int(saved["total"]) <= max_bytes:
                offset = 0
            if offset:
                with resume_path.open('rb') as prior:
                    if hashlib.file_digest(prior, 'sha256').hexdigest() != saved.get('partial_sha256'):
                        offset = 0
        except (ValueError, KeyError, TypeError, OSError):
            offset = 0
        if offset:
            headers.update(Range=f"bytes={offset}-", **{"If-Range": saved["validator"]})
    started = time.monotonic()
    try:
        with client.stream("GET", url, headers=headers, cookies=cookies) as response:
            if response.status_code not in (200, 206):
                # 오래된 서명 주소가 거부되어도 이미 받은 조각은 새 주소 요청 때 쓸 수 있습니다.
                return DownloadResult(url=url, status=response.status_code, content_type=response.headers.get("content-type"))
            length = response.headers.get("content-length")
            expected = int(length) if length and length.isdigit() else None
            if response.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                etag = response.headers.get("etag") or response.headers.get("last-modified")
                valid = bool(offset and match and int(match[1]) == offset and int(match[3]) == saved["total"]
                             and int(match[2]) == int(match[3]) - 1 and (not etag or etag == saved["validator"]))
                if not valid:
                    if resume_path:
                        resume_path.unlink(missing_ok=True)
                        metadata_path.unlink(missing_ok=True)
                    raise AppError("INVALID_RANGE", "이어받기 응답 범위가 일치하지 않아 조각을 폐기했습니다.")
                expected = int(match[3])
            else:
                offset = 0  # If-Range 불일치 또는 Range 미지원: 새 파일로 안전하게 시작합니다.
            if expected is not None and expected > max_bytes:
                raise AppError("MEDIA_TOO_LARGE", f"파일 크기({expected:,} bytes)가 한도를 넘습니다.", retryable=False)
            validator = response.headers.get("etag", "")
            if not validator or validator.startswith("W/"):
                validator = response.headers.get("last-modified", "")
            if response.status_code == 206:
                validator = validator or saved["validator"]
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise AppError("INVALID_RANGE", "압축된 부분 응답은 안전하게 이어받을 수 없습니다.")
            resumable = bool(resume_path and expected and validator and response.headers.get("content-encoding", "identity") == "identity")
            target = None
            if resumable:
                resume_path.parent.mkdir(parents=True, exist_ok=True)
                target = resume_path.open("ab" if offset else "wb")
                metadata_path.write_text(json.dumps({"validator": validator, "total": expected}), encoding="utf-8")
            chunks: list[bytes] = []
            total = offset
            try:
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if stop_event is not None and stop_event.is_set():
                        raise AppError("STOPPED", "중지 요청으로 다운로드를 끊었습니다.")
                    total += len(chunk)
                    if total > max_bytes:
                        raise AppError("MEDIA_TOO_LARGE", "파일 크기가 한도를 넘습니다.", retryable=False)
                    if target:
                        target.write(chunk)
                    else:
                        chunks.append(chunk)
                    if (max_duration_seconds is not None and time.monotonic() - started >= max_duration_seconds
                            and (expected is None or total < expected)):
                        code = "DOWNLOAD_PAUSED" if resumable and total > offset else "DOWNLOAD_TIME_LIMIT"
                        error = AppError(code, "전송 시간 한도로 이월했습니다. 검증 가능한 조각은 다음 실행에서 이어받습니다.")
                        error.progress = {'received_bytes':total, 'resumed_bytes':offset, 'transport_status':response.status_code}
                        raise error
            finally:
                if target:
                    target.close()
                    with resume_path.open('rb') as prior:
                        digest = hashlib.file_digest(prior, 'sha256').hexdigest()
                    metadata_path.write_text(json.dumps({'validator':validator, 'total':expected,
                                                         'partial_sha256':digest}), encoding='utf-8')
            if expected is not None and total != expected:
                raise AppError("INCOMPLETE_DOWNLOAD", "응답 크기와 수신 크기가 달라 완료 처리하지 않았습니다.")
            data = resume_path.read_bytes() if resumable else b"".join(chunks)
            if resume_path:
                resume_path.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
            return DownloadResult(url=url, status=200, data=data,
                                  content_type=response.headers.get("content-type"),
                                  headers={**{k.lower(): v for k, v in response.headers.items()},
                                           'x-archive-resumed-bytes': str(offset),
                                           'x-archive-transport-status': str(response.status_code)})
    except httpx.HTTPError as exc:
        raise AppError("NETWORK_ERROR", f"미디어 요청에 실패했습니다: {type(exc).__name__}") from None
    except OSError as exc:
        raise AppError("PARTIAL_WRITE_FAILED", f"다운로드 조각 보관 실패: {type(exc).__name__}") from None


def make_media_client(user_agent: str, timeout_seconds: float, connect_timeout: float = 5) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"},
        timeout=httpx.Timeout(timeout_seconds, connect=connect_timeout),
        follow_redirects=True,
    )
