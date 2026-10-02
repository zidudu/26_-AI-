"""미디어 다운로드. 서명 URL은 약 1시간 만료라 수집 직후 바로 내려받습니다.

CDN 규칙(2026-09 확인):
- 변환본: `https://ac.arca.live/...?expires=&key=`  (png → webp, gif → mp4 로 변환되어 내려옴)
- 원본:   같은 URL에 `&type=orig` (호스트 `ac-o.arca.live` 또는 `ac.arca.live` 모두 동작)
- 일부 원본 후보는 403/404로 실패함 → 제공본을 보관할 수 있으나 원본의 영구 부재를 증명하지는 않음
- 원본 요청은 CDN 캐시가 없어 느리고 간헐적으로 실패함 → 실패하면 변환본을 받고, 다음 실행에서 원본을 다시 시도

상태 표기:
- is_original = 1 원본, 0 변환본, NULL 미확인
- original_unavailable = 1 이면 현재 후보가 거부되어 정책상 원본 자동 재시도를 중단함
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode

from ..common import AppError, atomic_write_bytes, iso_after, now_iso, parse_iso, sha256_bytes, utcnow
from ..parsers.article_page import expires_of
from ..media_integrity import FORMAT_MIME, detected_format, original_format_mismatch
from ..storage import guess_extension, media_file_path
from .retry import schedule_retry
from .runner import CrawlContext

_ACCEPTED_TYPES = ("image/", "video/", "application/octet-stream", "binary/octet-stream")
ORIGINAL_BACKOFF_MINUTES = (30, 60, 120, 240, 360)
ORIGINAL_FAIL_STREAK = 3
_CONNECTION_CODES = ("NETWORK_ERROR", "NETWORK_TIMEOUT")


def originals_allowed(db) -> bool:
    """원본 서버 차단기: 연결 실패가 반복돼 잠시 원본 요청을 건너뛰는 중이면 False."""
    until = parse_iso(db.get_setting("orig_backoff_until") or "")
    return not (until and until > utcnow())


def original_backoff_status(db) -> dict:
    until = db.get_setting("orig_backoff_until") or ""
    parsed = parse_iso(until)
    active = bool(parsed and parsed > utcnow())
    return {"active": active, "until": until if active else None, "level": int(db.get_setting("orig_backoff_level") or 0)}


def _note_original_outcome(ctx: CrawlContext, failures: list, success: bool) -> None:
    db = ctx.db
    if success:
        ctx.original_fail_streak = 0
        if db.get_setting("orig_backoff_until"):
            db.set_setting("orig_backoff_until", "")
            db.set_setting("orig_backoff_level", "0")
            ctx.event("info", "original_endpoint_recovered")
        return
    if failures and all(a.code in _CONNECTION_CODES for a in failures):
        ctx.original_fail_streak += 1
        if ctx.original_fail_streak >= ORIGINAL_FAIL_STREAK:
            level = int(db.get_setting("orig_backoff_level") or 0)
            minutes = ORIGINAL_BACKOFF_MINUTES[min(level, len(ORIGINAL_BACKOFF_MINUTES) - 1)]
            db.set_setting("orig_backoff_until", iso_after(minutes * 60))
            db.set_setting("orig_backoff_level", str(level + 1))
            ctx.original_fail_streak = 0
            if "ORIGINAL_ENDPOINT_DOWN" not in ctx.stats.warnings:
                ctx.stats.warnings.append("ORIGINAL_ENDPOINT_DOWN")
            ctx.event("warning", "original_endpoint_down", minutes=minutes,
                      message=f"원본 서버 연결 실패가 {ORIGINAL_FAIL_STREAK}회 연속이라 {minutes}분간 변환본만 받습니다.")
    else:
        ctx.original_fail_streak = 0
DOWNLOADABLE_KINDS = ("image", "gif", "video", "emoticon")
MAX_UPGRADE_ATTEMPTS = 3


def original_candidates(media: dict) -> list[str]:
    """원본을 받을 수 있는 URL 후보(우선순위순)."""
    urls: list[str] = []
    if media.get("original_url"):
        urls.append(media["original_url"])
    served = media.get("served_url")
    if served and (urlsplit(served).hostname or '').endswith('.arca.live') and "type=orig" not in served:
        urls.append(served + ("&" if "?" in served else "?") + "type=orig")
    return [u for i, u in enumerate(urls) if u not in urls[:i] and not _url_is_expired(u)]


def converted_candidates(media: dict) -> list[str]:
    url = media.get("served_url")
    return [url] if url and not _url_is_expired(url) else []


def _url_is_expired(url: str) -> bool:
    expires = parse_iso(expires_of(url))
    return bool(expires and expires <= utcnow() + timedelta(seconds=60))


def _is_expired(media: dict) -> bool:
    # 원문에 오래된 data-originalurl이 남아 있어도 src는 새 서명으로 갱신될
    # 수 있습니다. 한 주소의 만료 때문에 다른 유효한 주소까지 막지 않습니다.
    urls = [media[key] for key in ("served_url", "original_url") if media.get(key)]
    if urls:
        return all(_url_is_expired(url) for url in urls)
    expires = parse_iso(media.get("url_expires_at"))
    return bool(expires and expires <= utcnow() + timedelta(seconds=60))


def _fail(ctx: CrawlContext, media: dict, code: str, message: str, retryable: bool = True) -> None:
    attempts = int(media.get("attempts") or 0) + 1
    state, next_retry = schedule_retry(attempts, ctx.settings.retry) if retryable else ("error", None)
    ctx.db.update_media(media["id"], state=state, state_code=code, state_message=message[:300], attempts=attempts,
                        next_retry_at=next_retry)
    ctx.stats.media_failed += 1
    ctx.event("warning" if state == "failed" else "error", "media_failed", article_id=media["article_id"],
              media_id=media["id"], code=code, message=message, attempts=attempts)


class _Attempt:
    """후보 URL 하나를 시도한 결과."""

    def __init__(self, url: str, result=None, code: str | None = None, message: str = "", retryable: bool = True,
                 definitive: bool = False):
        self.url = url
        self.result = result
        self.code = code
        self.message = message
        self.retryable = retryable
        self.definitive = definitive  # 403/404 등 현재 정책에서 자동 재시도를 제한하는 응답

    @property
    def ok(self) -> bool:
        return self.result is not None


def _try_url(ctx: CrawlContext, article: dict, url: str) -> _Attempt:
    settings = ctx.settings
    ctx.media_limiter.wait()
    if "type=orig" in url:
        ctx.original_limiter.wait()
    ctx.check_stop()
    started = time.monotonic()
    try:
        ctx.fetcher.download_timeout_seconds = ctx.download_seconds_remaining()
        parts = urlsplit(url)
        stable_url = parts._replace(query=urlencode(sorted((k, v) for k, v in parse_qsl(parts.query)
                                                            if k not in ("expires", "key", "signature")))).geturl()
        ctx.fetcher.download_resume_path = settings.data_path / "partials" / f"{ctx.active_media_id}-{sha256_bytes(stable_url.encode())[:20]}.part"
        result = ctx.fetcher.download(url, referer=article["url"], max_bytes=settings.media.max_file_mb * 1024 * 1024)
    except AppError as exc:
        ctx.event("warning", "media_request", article_id=article["id"], media_id=ctx.active_media_id,
                  code=exc.code, seconds=round(time.monotonic() - started, 3), original="type=orig" in url,
                  progress=getattr(exc,'progress',None))
        if exc.stop:
            raise
        if exc.code == "STOPPED" or ctx.stop_event.is_set():
            ctx.check_stop()
        return _Attempt(url, code=exc.code, message=exc.message, retryable=exc.retryable)
    ctx.event("info", "media_request", article_id=article["id"], media_id=ctx.active_media_id,
              status=result.status, seconds=round(time.monotonic() - started, 3),
              bytes=len(result.data), resumed_bytes=int(result.headers.get('x-archive-resumed-bytes',0)),
              transport_status=int(result.headers.get('x-archive-transport-status',result.status)), original="type=orig" in url)
    if result.status in (401, 403, 410):
        return _Attempt(url, code="URL_REJECTED", message=f"CDN이 요청을 거부했습니다(HTTP {result.status}).", definitive=True)
    if result.status == 404:
        return _Attempt(url, code="MEDIA_NOT_FOUND", message="미디어 파일이 없습니다(HTTP 404).", retryable=False, definitive=True)
    if not result.ok:
        return _Attempt(url, code=f"HTTP_{result.status}", message=f"미디어 응답 오류(HTTP {result.status}).")
    content_type = (result.content_type or "").split(";")[0].strip().lower()
    if content_type and not content_type.startswith(_ACCEPTED_TYPES):
        return _Attempt(url, code="UNEXPECTED_CONTENT_TYPE", message=f"미디어가 아닌 응답({content_type})을 받았습니다.")
    return _Attempt(url, result=result)


def _store(ctx: CrawlContext, article: dict, media: dict, attempt: _Attempt, is_original: bool | None) -> bool:
    """받은 바이트를 파일로 저장하고 DB를 갱신합니다. 실패 시 False."""
    db, settings = ctx.db, ctx.settings
    result = attempt.result
    detected = detected_format(result.data[:64])
    content_type = FORMAT_MIME.get(detected) or (result.content_type or "").split(";")[0].strip().lower()
    sha = sha256_bytes(result.data)
    ext = detected or guess_extension(urlsplit(attempt.url).path, content_type, result.data[:16])
    path = media_file_path(settings, ctx.channel["slug"], article["id"], media["seq"], sha, ext)
    existing = db.find_media_by_sha(sha)
    deduplicated = False
    try:
        if existing and existing.get("file_path") and Path(existing["file_path"]).exists() and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(existing["file_path"], path)
                deduplicated = True
            except OSError:
                atomic_write_bytes(path, result.data)
        elif not path.exists():
            atomic_write_bytes(path, result.data)
    except OSError as exc:
        _fail(ctx, media, "FILE_WRITE_FAILED", f"파일 저장 실패: {type(exc).__name__}")
        return False
    previous_path = media.get("file_path")
    original_fields = {"original_unavailable": 0, "upgrade_attempts": 0} if is_original else {}
    db.update_media(media["id"], state="downloaded", state_code=None, state_message=None, file_path=str(path),
                    file_size=len(result.data), sha256=sha, content_type=content_type or None,
                    downloaded_at=now_iso(), next_retry_at=None, is_original=None if is_original is None else int(is_original), **original_fields)
    if previous_path and previous_path != str(path):
        _remove_if_unreferenced(ctx, previous_path)
    ctx.stats.media_downloaded += 1
    if media["state"] != "downloaded":
        if media["id"] <= ctx.initial_media["max_id"]:
            ctx.stats.media_existing_downloaded += 1
        else:
            ctx.stats.media_new_downloaded += 1
    if deduplicated:
        ctx.stats.media_deduplicated += 1
    ctx.event("info", "media_downloaded", article_id=article["id"], media_id=media["id"], kind=media["kind"],
              size=len(result.data), original=is_original, ext=ext)
    ctx.checkpoint()
    return True


def _remove_if_unreferenced(ctx: CrawlContext, path: str) -> None:
    """변환본을 원본으로 교체한 뒤, 다른 항목이 쓰지 않는 옛 파일을 지웁니다."""
    if ctx.db.media_referencing_path(path):
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def download_media_for_article(ctx: CrawlContext, article: dict | None) -> dict:
    """글에 연결된 대기 중 미디어를 내려받고, 변환본만 있는 항목은 원본 승격을 시도합니다."""
    if article is None:
        return {}
    db = ctx.db
    settings = ctx.settings
    pending = db.pending_media_for_article(article["id"])
    upgrades = (db.upgrade_candidates_for_article(article["id"], MAX_UPGRADE_ATTEMPTS)
                if settings.media.request_original and originals_allowed(db) else [])
    done = 0
    per_article_limit = settings.media.max_files_per_article_per_run
    if ctx.work_kind == "backlog":
        backlog_limit = settings.media.backlog_files_per_article_by_channel.get(
            ctx.channel["slug"], settings.media.backlog_files_per_article)
        per_article_limit = min(per_article_limit, backlog_limit)
        upgrades = []
    for index, media in enumerate(pending):
        ctx.check_stop()
        if index >= per_article_limit or ctx.media_budget_exhausted():
            remaining = len(pending) - index
            ctx.stats.media_deferred += remaining
            ctx.event("info", "media_deferred", article_id=article["id"], remaining=remaining,
                      reason="PER_ARTICLE_LIMIT" if index >= per_article_limit else "TIME_BUDGET")
            break
        if media["kind"] not in DOWNLOADABLE_KINDS:
            db.update_media(media["id"], state="skipped", state_code="NOT_DOWNLOADABLE")
            ctx.stats.media_skipped += 1
            continue
        ctx.stats.media_targets += 1
        if _is_expired(media):
            db.update_media(media["id"], state="expired", state_code="URL_EXPIRED",
                            state_message="서명 URL이 만료되었습니다. 글을 다시 열어 URL을 갱신합니다.")
            ctx.stats.media_expired += 1
            continue
        if _download_one(ctx, article, media):
            done += 1

    for media in upgrades:
        ctx.check_stop()
        if ctx.media_budget_exhausted():
            break
        if _is_expired(media):
            continue
        _upgrade_one(ctx, article, media)
    return {"downloaded": done, "pending": len(pending), "upgrades": len(upgrades)}


def _original_attempt(ctx: CrawlContext, article: dict, media: dict, url: str) -> tuple[_Attempt, _Attempt | None]:
    """Return the original verdict and any usable converted response separately."""
    attempt = _try_url(ctx, article, url)
    mismatch = original_format_mismatch(media, attempt.result.data[:64]) if attempt.ok else None
    if mismatch:
        expected, actual = mismatch
        ctx.event("warning", "original_format_mismatch", article_id=article["id"], media_id=media["id"],
                  expected=expected, actual=actual)
        return _Attempt(url, code="ORIGINAL_FORMAT_MISMATCH",
                        message=f"원본 형식({expected})과 받은 파일 형식({actual})이 다릅니다."), attempt
    return attempt, None


def _download_one(ctx: CrawlContext, article: dict, media: dict) -> bool:
    ctx.active_article_id, ctx.active_media_id = article["id"], media["id"]
    db, settings = ctx.db, ctx.settings
    want_original = settings.media.request_original and not media.get("original_unavailable")
    skipped_by_breaker = want_original and not originals_allowed(db)
    originals = original_candidates(media) if want_original and not skipped_by_breaker else []
    converted = converted_candidates(media)
    if not originals and not converted:
        _fail(ctx, media, "NO_URL", "다운로드할 URL이 없습니다.", retryable=False)
        return False

    original_failures: list[_Attempt] = []
    received_converted: _Attempt | None = None
    for url in originals:
        attempt, converted_response = _original_attempt(ctx, article, media, url)
        if converted_response and received_converted is None:
            received_converted = converted_response
        if attempt.ok:
            _note_original_outcome(ctx, [], success=True)
            return _store(ctx, article, media, attempt, is_original=True)
        original_failures.append(attempt)
        if attempt.code == "DOWNLOAD_PAUSED":
            _defer_partial(ctx, media)
            return False
        if attempt.code in _CONNECTION_CODES:
            break  # 연결 자체가 안 되면 다른 원본 후보도 같은 서버라 더 기다리지 않습니다.

    if originals:
        _note_original_outcome(ctx, original_failures, success=False)
        # 모든 후보가 거부된 경우(403/404 등) 자동 재시도를 제한합니다.
        # 형식 불일치는 영구 부재의 증거가 아니므로 기존 한도 내에서 재시도합니다.
        unavailable = all(a.definitive for a in original_failures)
        connection_problem = all(a.code in _CONNECTION_CODES for a in original_failures)
        # 서버 연결 문제는 항목 탓이 아니므로 승격 시도 횟수를 소모하지 않습니다.
        consumed = 0 if (unavailable or connection_problem) else 1
        db.update_media(media["id"], original_unavailable=1 if unavailable else 0,
                        upgrade_attempts=int(media.get("upgrade_attempts") or 0) + consumed)
        ctx.event("info", "media_fallback", article_id=article["id"], media_id=media["id"],
                  codes=[a.code for a in original_failures], original_unavailable=unavailable)

    if received_converted is not None:
        # Keep the usable bytes without downloading the same converted file twice.
        return _store(ctx, article, media, received_converted, is_original=False)

    last: _Attempt | None = None
    for url in converted:
        attempt = _try_url(ctx, article, url)
        if attempt.ok:
            # 차단기로 원본을 건너뛴 경우도 변환본(is_original=0)으로 기록해 나중에 승격 대상이 됩니다.
            return _store(ctx, article, media, attempt, is_original=False if (originals or media.get("original_url")) else None)
        last = attempt
        if attempt.code == "DOWNLOAD_PAUSED":
            _defer_partial(ctx, media)
            return False
    if last is None and original_failures:
        last = original_failures[-1]
    if last is not None:
        if last.code == "URL_REJECTED":
            _fail(ctx, media, last.code, last.message)
        else:
            _fail(ctx, media, last.code or "DOWNLOAD_FAILED", last.message, last.retryable)
    return False


def _defer_partial(ctx, media):
    ctx.db.update_media(media["id"], state="pending", state_code="DOWNLOAD_PAUSED",
                        state_message="전송 시간 한도로 이월했습니다. 다음 실행에서 검증 후 이어받습니다.",
                        next_retry_at=iso_after(60))
    ctx.stats.media_deferred += 1


def _upgrade_one(ctx: CrawlContext, article: dict, media: dict) -> bool:
    """변환본으로 저장된 항목의 원본을 다시 시도합니다."""
    db = ctx.db
    ctx.active_article_id, ctx.active_media_id = article["id"], media["id"]
    if not originals_allowed(db):
        return False
    failures: list[_Attempt] = []
    for url in original_candidates(media):
        attempt, _ = _original_attempt(ctx, article, media, url)
        if attempt.ok:
            _note_original_outcome(ctx, [], success=True)
            if not _store(ctx, article, media, attempt, is_original=True):
                return False
            ctx.stats.media_upgraded += 1
            ctx.event("info", "media_upgraded", article_id=article["id"], media_id=media["id"])
            ctx.checkpoint()
            return True
        failures.append(attempt)
        if attempt.code == "DOWNLOAD_PAUSED":
            ctx.stats.media_deferred += 1
            return False
        if attempt.code in _CONNECTION_CODES:
            break
    _note_original_outcome(ctx, failures, success=False)
    if failures and all(a.code in _CONNECTION_CODES for a in failures):
        return False  # 서버 연결 문제는 항목 탓이 아니므로 승격 시도 횟수를 소모하지 않습니다.
    unavailable = bool(failures) and all(a.definitive for a in failures)
    db.update_media(media["id"], original_unavailable=1 if unavailable else 0,
                    upgrade_attempts=int(media.get("upgrade_attempts") or 0) + 1)
    ctx.event("info", "media_upgrade_failed", article_id=article["id"], media_id=media["id"],
              codes=[a.code for a in failures], original_unavailable=unavailable)
    return False
