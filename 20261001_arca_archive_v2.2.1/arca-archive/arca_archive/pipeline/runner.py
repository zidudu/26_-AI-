"""실행(run) 오케스트레이션: 컨텍스트, 단계 진행, 상태 확정.

한 실행은 `discover → collect → recheck` 단계로 진행합니다.
- 항목 단위 실패는 기록하고 계속합니다.
- 세션 수준 실패(AppError.stop)는 실행을 중단하고 원인 코드를 남깁니다.
- 중지 요청(stop_event)은 항목/페이지 경계에서 확인합니다.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field

from ..common import AppError, RunLock, iso_after, now_iso
from ..config import Settings
from ..db import Database
from ..fetch.base import FetchResult
from ..fetch.ratelimit import RateLimiter
from ..parsers.page_state import PageState
from ..sites import get_site

log = logging.getLogger("arca.runner")


class StopRequested(Exception):
    pass


@dataclass
class RunStats:
    pages_scanned: int = 0
    discovered_new: int = 0
    discover_reason: str | None = None
    coverage_complete: bool = True
    collect_targets: int = 0
    collected: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    blocked: int = 0
    failed: int = 0
    errors: int = 0
    rechecked: int = 0
    media_targets: int = 0
    media_downloaded: int = 0
    media_deduplicated: int = 0
    media_failed: int = 0
    media_expired: int = 0
    media_skipped: int = 0
    media_deferred: int = 0
    media_upgraded: int = 0
    media_existing_downloaded: int = 0
    media_new_downloaded: int = 0
    media_created: int = 0
    unfinished_before: int = 0
    unfinished_after: int | None = None  # 종료 때 측정합니다. 실행 중 0건으로 오인하지 않게 합니다.
    collect_deferred: int = 0
    comments_saved: int = 0
    comments_incomplete: int = 0
    fetcher_switches: int = 0
    logged_in: bool | None = None  # 브라우저로 읽은 목록 페이지에서 관측한 로그인 여부
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def has_item_failures(self) -> bool:
        return bool(self.failed or self.errors or self.media_failed or self.media_expired or self.comments_incomplete)

    @property
    def has_deferred(self) -> bool:
        return bool(self.media_deferred or self.collect_deferred)


ESCALATE_KINDS = {"challenge", "legal_block", "login_required"}


class CrawlContext:
    """한 실행 동안 공유되는 상태: fetcher, 속도 제한, 이벤트 기록."""

    def __init__(self, settings: Settings, db: Database, run: dict, channel: dict,
                 stop_event: threading.Event | None = None, fetcher_factory=None):
        self.settings = settings
        self.db = db
        self.run = run
        self.run_id = run["id"]
        self.channel = channel
        self.site = get_site(channel.get("site"))
        self.stop_event = stop_event or threading.Event()
        self.stats = RunStats()
        policy = self.site.policy
        self.page_limiter = RateLimiter(max(settings.http.page_delay_seconds, policy.min_page_delay),
                                        max(settings.http.page_delay_jitter_seconds, policy.page_delay_jitter),
                                        interrupt=self.stop_event)
        self.media_limiter = RateLimiter(settings.http.media_delay_seconds, interrupt=self.stop_event)
        self.original_limiter = RateLimiter(settings.media.original_delay_seconds, interrupt=self.stop_event)
        self.original_fail_streak = 0
        self.fetcher = None
        self.fetcher_name: str | None = None
        self.consecutive_network_errors = 0
        self._fetcher_factory = fetcher_factory or self._default_factory
        self.stage = "start"
        self.work_kind = "backlog" if run.get("trigger") in ("backlog", "backlog_manual") else "collect"
        self.active_article_id = None
        self.active_media_id = None
        self.initial_media = db.media_snapshot(channel["id"])
        self.stats.unfinished_before = self.initial_media["unfinished"]
        self.started_monotonic = time.monotonic()
        self.stage_started_monotonic = time.monotonic()
        self.touched_articles: set[int] = set()  # 이번 실행에서 이미 열어본 글(recheck 중복 방지)

    def media_budget_exhausted(self) -> bool:
        if self.work_kind == "backlog":
            return time.monotonic() - self.started_monotonic > self.backlog_time_budget_seconds()
        over = (time.monotonic() - self.started_monotonic) > self.settings.media.run_time_budget_minutes * 60
        if over and self.stage == "recheck":
            # 밀린 미디어를 처리하는 재확인 단계에는 최소 시간을 보장해 이월분이 계속 쌓이지 않게 합니다.
            return (time.monotonic() - self.stage_started_monotonic) > self.settings.media.recheck_reserve_minutes * 60
        return over

    def download_seconds_remaining(self) -> float:
        if self.work_kind == "backlog":
            budget = self.backlog_time_budget_seconds() - (time.monotonic() - self.started_monotonic)
        elif self.stage == "recheck":
            budget = max(self.settings.media.run_time_budget_minutes * 60 - (time.monotonic() - self.started_monotonic),
                         self.settings.media.recheck_reserve_minutes * 60 - (time.monotonic() - self.stage_started_monotonic))
        else:
            budget = self.settings.media.run_time_budget_minutes * 60 - (time.monotonic() - self.started_monotonic)
        return max(1, min(self.settings.media.max_download_seconds, budget))

    def backlog_time_budget_seconds(self) -> int:
        media = self.settings.media
        minutes = media.backlog_time_budget_by_channel.get(self.channel["slug"], media.backlog_time_budget_minutes)
        return minutes * 60

    # ------------------------------------------------------------------ fetcher
    def _default_factory(self, kind: str):
        if kind == "browser":
            from ..fetch.browser_fetcher import BrowserFetcher

            policy = self.site.policy
            fetcher = BrowserFetcher(self.settings, block_resources=not policy.natural_page_loads)
            fetcher.in_page_downloads = policy.in_page_media
            fetcher.stop_event = self.stop_event
            fetcher.start()
            return fetcher
        from ..fetch.http_fetcher import HttpFetcher

        fetcher = HttpFetcher(self.settings)
        fetcher.stop_event = self.stop_event
        return fetcher

    def use_fetcher(self, kind: str) -> None:
        if self.fetcher is not None and self.fetcher_name == kind:
            return
        self.close_fetcher()
        self.fetcher = self._fetcher_factory(kind)
        self.fetcher_name = kind
        self.db.update_run(self.run_id, fetcher=kind)

    def close_fetcher(self) -> None:
        if self.fetcher is not None:
            try:
                self.fetcher.close()
            except Exception:  # pragma: no cover - 종료 실패는 무시
                pass
        self.fetcher = None
        self.fetcher_name = None

    def escalate_to_browser(self, reason: str) -> bool:
        """HTTP → 브라우저 전환. 이미 브라우저이거나 설정이 http 고정이면 False."""
        if self.fetcher_name == "browser" or self.channel.get("fetch_mode") == "http":
            return False
        self.event("info", "fetcher_switch", reason=reason, to="browser")
        self.stats.fetcher_switches += 1
        self.use_fetcher("browser")
        if not self.channel.get("requires_browser"):
            self.db.update_channel(self.channel["id"], requires_browser=1)
            self.channel["requires_browser"] = 1
        return True

    # ------------------------------------------------------------------ 진행
    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested()

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        self.stage_started_monotonic = time.monotonic()
        self.db.update_run(self.run_id, stats=self.stats.as_dict(), stage=stage)
        self.event("info", "stage", stage=stage)

    def checkpoint(self) -> None:
        self.db.update_run(self.run_id, stats=self.stats.as_dict())

    def event(self, level: str, event: str, article_id: int | None = None, media_id: int | None = None, **data) -> None:
        try:
            self.db.add_event(self.run_id, level, event, article_id=article_id, media_id=media_id, **data)
        except Exception:  # pragma: no cover
            log.exception("이벤트 기록 실패")
        text = f"run={self.run_id} {event} " + " ".join(f"{k}={v}" for k, v in data.items() if k != "message")
        if data.get("message"):
            text += f" | {data['message']}"
        getattr(log, "warning" if level == "warning" else ("error" if level == "error" else "info"))(text)

    # ------------------------------------------------------------------ 페이지 요청
    def fetch_page_checked(self, url: str, kind: str = "article") -> tuple[FetchResult, PageState]:
        """(호환용) HTML 페이지 요청."""
        from ..sites.base import Request

        return self.fetch_checked(Request(url=url, kind="html"))

    def fetch_checked(self, request) -> tuple[FetchResult, PageState]:
        """속도 제한 → 요청(html/json) → 사이트별 상태 분류. 세션 문제는 자동 전환 후 1회 재시도합니다."""
        for attempt in range(2):
            self.check_stop()
            if self.fetcher is None:
                self.use_fetcher("browser" if self.channel.get("requires_browser") or self.channel.get("fetch_mode") == "browser" else "http")
            self.page_limiter.wait()
            self.check_stop()
            effective = request
            if request.kind == "page_capture" and self.fetcher_name != "browser":
                effective = request.fallback or request
            try:
                if effective.kind == "page_capture":
                    result = self.fetcher.fetch_page_capture(effective.url, effective.capture_pattern or ".",
                                                             session_cookies=self.site.session_cookie_names())
                elif effective.kind == 'form':
                    result = self.fetcher.fetch_form(effective.url, effective.data, effective.headers)
                elif effective.kind == "json":
                    if self.fetcher_name == "browser":
                        result = self.fetcher.fetch_api(effective.url, effective.headers, origin=effective.origin)
                    else:
                        result = self.fetcher.fetch_api(effective.url, effective.headers)
                else:
                    result = self.fetcher.fetch_page(effective.url)
            except AppError as exc:
                self.event("warning", "page_request_failed", article_id=self.active_article_id,
                           code=exc.code, request_kind=effective.kind)
                if exc.stop:
                    raise
                self.consecutive_network_errors += 1
                if self.consecutive_network_errors >= self.settings.http.max_consecutive_network_errors:
                    raise AppError("NETWORK_UNSTABLE", "네트워크 오류가 연속으로 발생해 실행을 중단합니다.", stop=True) from None
                raise
            self.consecutive_network_errors = 0
            state = self.site.classify(result, effective.kind)
            if state.kind != "ok":
                from urllib.parse import urlsplit
                parts = urlsplit(effective.url)
                self.event("warning", "page_response", article_id=self.active_article_id,
                           status=result.status, code=state.code, request_kind=effective.kind,
                           host=parts.hostname, path=parts.path)
            if state.kind in ESCALATE_KINDS and attempt == 0 and self.fetcher_name == "http":
                if self.escalate_to_browser(state.code):
                    continue
            return result, state
        raise AppError("FETCH_LOOP", "요청 재시도 한도를 넘었습니다.")  # pragma: no cover


def initial_fetcher_kind(channel: dict) -> str:
    mode = channel.get("fetch_mode") or "auto"
    if mode == "browser" or channel.get("requires_browser"):
        return "browser"
    if mode == "auto" and get_site(channel.get("site")).policy.prefer_browser:
        return "browser"
    return "http"


def run_channel(settings: Settings, db: Database, channel_id: int, trigger: str = "manual",
                stop_event: threading.Event | None = None, fetcher_factory=None,
                on_progress=None, on_run_created=None) -> dict:
    lock = RunLock(settings.lock_path)
    if not lock.acquire():
        raise AppError("ALREADY_RUNNING", "다른 수집 작업이 실행 중입니다(잠금 획득 실패).", stop=True, retryable=False)
    try:
        return _run_channel_locked(settings, db, channel_id, trigger, stop_event, fetcher_factory,
                                   on_progress, on_run_created)
    finally:
        lock.release()


def _run_channel_locked(settings, db, channel_id, trigger, stop_event, fetcher_factory, on_progress, on_run_created):
    """채널 하나를 수집합니다. 반환: 완료된 run 레코드."""
    from .collect import collect_article
    from .discover import discover
    from .media import download_media_for_article
    from .recheck import recheck

    channel = db.get_channel(channel_id)
    if channel is None:
        raise AppError("CHANNEL_NOT_FOUND", f"채널 id={channel_id} 이(가) 없습니다.", stop=True, retryable=False)
    run = db.create_run(channel, trigger)
    if on_run_created:
        on_run_created(run["id"])
    ctx = CrawlContext(settings, db, run, channel, stop_event, fetcher_factory)
    status, code, message = "success", "OK", None
    try:
        try:
            ctx.use_fetcher(initial_fetcher_kind(channel))
            ctx.event("info", "run_started", channel=channel["slug"], trigger=trigger, fetcher=ctx.fetcher_name)

            if ctx.work_kind != "backlog":
                ctx.set_stage("discover")
                discover(ctx)
                ctx.checkpoint()

            ctx.set_stage("backlog" if ctx.work_kind == "backlog" else "collect")
            article_cap = settings.crawl.max_articles_per_run
            if ctx.site.policy.max_articles_per_run:
                article_cap = min(article_cap, ctx.site.policy.max_articles_per_run)
            pending = [] if ctx.work_kind == "backlog" else db.pending_articles(channel_id, article_cap)
            ctx.stats.collect_targets = len(pending)
            for index, article in enumerate(pending, 1):
                ctx.check_stop()
                if ctx.media_budget_exhausted():
                    ctx.stats.collect_deferred = len(pending) - index + 1
                    ctx.event("info", "collect_deferred", remaining=ctx.stats.collect_deferred, reason="TIME_BUDGET")
                    ctx.stats.warnings.append("RUN_TIME_BUDGET")
                    break
                outcome = collect_article(ctx, article)
                if outcome in ("collected", "updated", "unchanged") and channel.get("collect_media", 1):
                    download_media_for_article(ctx, db.get_article(article["id"]))
                ctx.checkpoint()  # 글마다 갱신해 GUI 상태 표시가 실시간에 가깝게 따라옵니다.
                if on_progress:
                    on_progress(ctx)
            ctx.checkpoint()

            ctx.set_stage("backlog" if ctx.work_kind == "backlog" else "recheck")
            recheck(ctx, backlog_only=ctx.work_kind == "backlog")
            ctx.checkpoint()
        finally:
            ctx.close_fetcher()
    except StopRequested:
        status, code, message = "cancelled", "CANCELLED", "사용자가 중지했습니다."
    except AppError as exc:
        if ctx.stop_event.is_set():
            # 중지 요청 뒤에 난 오류(브라우저 종료 등)는 취소로 기록합니다.
            status, code, message = "cancelled", "CANCELLED", "사용자가 중지했습니다."
        else:
            status, code, message = "failed", exc.code, exc.message
            ctx.event("error", "run_aborted", article_id=ctx.active_article_id, media_id=ctx.active_media_id,
                      code=exc.code, message=exc.message)
    except Exception as exc:  # pragma: no cover - 예상 밖 오류
        status, code, message = "failed", "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"
        log.error("실행 중 내부 오류\n%s", traceback.format_exc())
        ctx.event("error", "run_aborted", article_id=ctx.active_article_id, media_id=ctx.active_media_id,
                  code="INTERNAL_ERROR", message=str(exc)[:300])
    finally:
        ctx.close_fetcher()
    if status == "success" and (ctx.stats.has_item_failures or ctx.stats.has_deferred or not ctx.stats.coverage_complete):
        status, code = "partial", "PARTIAL"
        reasons = []
        if ctx.stats.has_item_failures:
            reasons.append('실패 항목 또는 미완료 댓글이 있습니다.')
        if ctx.stats.has_deferred:
            reasons.append('시간·개수 제한에 도달한 작업은 다음 실행으로 이월했습니다.')
        if not ctx.stats.coverage_complete:
            reasons.append('목록 범위를 다 확인하지 못했습니다.')
        message = ' '.join(reasons)
    final_media = db.media_snapshot(channel_id)
    ctx.stats.media_created = final_media["total"] - ctx.initial_media["total"]
    ctx.stats.unfinished_after = final_media["unfinished"]
    stats = ctx.stats.as_dict()
    db.finish_run(run["id"], status, code, message, stats)
    try:
        purged = db.purge_old_events(settings.crawl.keep_events_days)
        if purged:
            log.info("오래된 실행 이벤트 %d건 정리", purged)
    except Exception:  # pragma: no cover
        log.exception("이벤트 정리 실패")
    ctx.event("info", "run_finished", status=status, code=code, collected=stats["collected"],
              media_downloaded=stats["media_downloaded"], failed=stats["failed"])
    update: dict = {
        "last_run_id": run["id"], "last_run_at": now_iso(), "last_error_code": None if status in ("success", "partial") else code,
    }
    if ctx.work_kind == "backlog":
        update["next_media_at"] = iso_after(settings.media.backlog_interval_minutes * 60)
    else:
        update["next_run_at"] = iso_after(max(1, int(channel.get("interval_minutes") or settings.crawl.default_interval_minutes)) * 60)
    if status in ("success", "partial"):
        update["last_success_at"] = now_iso()
    elif code in ("LOGIN_REQUIRED", "FORBIDDEN", "BLOCKED_451", "CF_CHALLENGE", "RATE_LIMITED"):
        # 접근 제한은 신규 수집과 대기 처리 모두에 영향을 줍니다.
        retry_at = iso_after(max(30, int(channel.get("interval_minutes") or 60)) * 60)
        update.update(next_run_at=retry_at, next_media_at=retry_at)
    db.update_channel(channel_id, **update)
    return db.get_run(run["id"])  # type: ignore[return-value]
