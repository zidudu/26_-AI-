"""게시글 본문 수집: 요청 → 상태 분류 → 사이트별 파싱 → 저장(원본, 본문, 미디어 참조, 댓글)."""
from __future__ import annotations

from ..common import AppError, iso_after, now_iso
from ..parsers.page_state import is_logged_in
from ..sites.base import Request
from ..sites.base import ArticleParseError
from ..storage import save_raw_text
from .retry import schedule_retry
from .runner import CrawlContext


def _record_failure(ctx: CrawlContext, article: dict, code: str, message: str, retryable: bool = True) -> str:
    attempts = int(article.get("attempts") or 0) + 1
    if retryable:
        state, next_retry = schedule_retry(attempts, ctx.settings.retry)
    else:
        state, next_retry = "error", None
    ctx.db.mark_article_failure(article["id"], state, code, message, next_retry, attempts)
    if state == "error":
        ctx.stats.errors += 1
        ctx.event("error", "article_error", article_id=article["id"], code=code, message=message, attempts=attempts)
    else:
        ctx.stats.failed += 1
        ctx.event("warning", "article_failed", article_id=article["id"], code=code, message=message,
                  attempts=attempts, next_retry_at=next_retry)
    return state


def collect_article(ctx: CrawlContext, article: dict, recheck: bool = False) -> str:
    """한 글을 수집합니다. 반환: collected | updated | unchanged | deleted | blocked | failed | error"""
    db = ctx.db
    settings = ctx.settings
    channel = ctx.channel
    site = ctx.site
    ctx.active_article_id = article["id"]
    ctx.active_media_id = None
    ctx.touched_articles.add(article["id"])
    if not article.get("remote_id"):
        article = {**article, "remote_id": str(article["id"])}
    request = site.article_request(channel, article)
    try:
        result, state = ctx.fetch_checked(request)
    except AppError as exc:
        if exc.stop:
            raise
        return _record_failure(ctx, article, exc.code, exc.message, exc.retryable)
    if state.kind in ("deleted", "not_found"):
        db.mark_article_failure(article["id"], "deleted", state.code, state.message, None)
        ctx.stats.deleted += 1
        ctx.event("info", "article_deleted", article_id=article["id"], code=state.code)
        return "deleted"
    if state.kind == "restricted":
        # 가입·등급이 필요한 글: 우회하지 않고 기록만 남깁니다. 권한이 생기면 오류 페이지에서 다시 대기열에 넣습니다.
        db.mark_article_failure(article["id"], "blocked", state.code, state.message, None)
        ctx.stats.blocked += 1
        ctx.event("info", "article_blocked", article_id=article["id"], code=state.code, message=state.message)
        return "blocked"
    if state.kind == "legal_block" and recheck and article.get("state") == "collected":
        # 기존 본문과 파일 참조를 보존하고 글의 451 기록 및 다음 확인 시각을 남깁니다.
        # 로그인 상태가 확인되지 않으면 세션 전체의 제한일 수 있으므로 실행을 중단합니다.
        retry_at = iso_after(max(30, int(channel.get("interval_minutes") or 60)) * 60)
        db.mark_article_failure(article["id"], "collected", state.code, state.message, retry_at)
        authenticated = ctx.fetcher_name == "browser" and ctx.stats.logged_in is True
        if not authenticated and ctx.fetcher_name == "browser":
            # 같은 브라우저 세션에서 홈의 로그인 표시를 확인해야 글 단위 451로 판단할 수 있습니다.
            # 확인되지 않으면 세션 수준 제한으로 보고 기존처럼 실행을 중단합니다.
            try:
                home, home_state = ctx.fetch_checked(Request("https://arca.live/"))
                authenticated = home_state.kind == "ok" and is_logged_in(home.html) is True
            except AppError:
                authenticated = False
        ctx.event("warning", "article_access_blocked", article_id=article["id"],
                  code=state.code, next_retry_at=retry_at, authenticated=authenticated)
        if authenticated:
            ctx.stats.logged_in = True
            ctx.stats.blocked += 1
            return "blocked"
    if state.stop:
        raise AppError(state.code, state.message, stop=True)
    if state.kind != "ok":
        return _record_failure(ctx, article, state.code, state.message, state.retryable)
    media_hosts = site.media_hosts(settings.media.allowed_hosts)
    try:
        parsed = site.parse_article(result, article, channel, media_hosts, bool(channel.get("collect_comments", 1)))
    except ArticleParseError as exc:
        return _record_failure(ctx, article, "PARSE_" + exc.code, exc.message)
    raw_path = None
    if settings.crawl.keep_raw_html:
        try:
            body = (result.html or "").lstrip()
            ext = "json" if (request.kind in ("json", "page_capture") and body[:1] in ("{", "[")) else "html"
            raw_path = str(save_raw_text(settings, channel["slug"], article["id"], now_iso(), result.html, ext))
        except OSError as exc:
            ctx.event("warning", "raw_html_save_failed", article_id=article["id"], message=type(exc).__name__)
    saved = db.save_collected(article["id"], parsed.as_dict(), ctx.run_id, raw_path)

    if channel.get("collect_media", 1):
        items = []
        for item in parsed.media:
            item = dict(item)
            if item["kind"] == "emoticon" and not settings.media.include_emoticons:
                continue
            if item["kind"] == "external":
                item["state"] = "skipped"
            items.append(item)
        if items:
            db.upsert_media(article["id"], items)
        missing = db.reconcile_body_media(article["id"], {m["source_key"] for m in parsed.media})
        if missing:
            ctx.event("warning", "media_source_missing", article_id=article["id"], count=missing)
    if channel.get("collect_comments", 1) and parsed.comments:
        rows = []
        for c in parsed.comments:
            rows.append({
                "id": site.internal_comment_id(channel, c["remote_id"]),
                "remote_id": c["remote_id"],
                "parent_id": site.internal_comment_id(channel, c["parent_remote_id"]) if c.get("parent_remote_id") else None,
                "author": c.get("author"), "created_at": c.get("created_at"), "body_html": c.get("body_html"),
                "body_text": c.get("body_text"), "is_deleted": c.get("is_deleted"),
            })
        ctx.stats.comments_saved += db.replace_comments(article["id"], rows)

    if channel.get('collect_comments', 1) and hasattr(site, 'comments_request'):
        from .comments import collect_comment_pages
        if parsed.comment_count != 0:
            previous = db.comment_fetch(article['id']) or {}
            retry_later = (previous.get('state') != 'complete' and previous.get('next_retry_at')
                           and previous['next_retry_at'] > now_iso())
            if previous.get('state') == 'error' or retry_later:
                ctx.stats.comments_incomplete += 1
                ctx.event('info', 'comments_retry_deferred', article_id=article['id'],
                          reason='RETRY_LIMIT' if previous.get('state') == 'error' else 'BACKOFF')
            else:
                collect_comment_pages(ctx, article, result, parsed.comment_count or 0)
        else:
            db.save_comment_fetch(article['id'], 'complete', 0, 0)

    for warning in parsed.warnings:
        if warning not in ctx.stats.warnings:
            ctx.stats.warnings.append(warning)

    if recheck:
        ctx.stats.rechecked += 1
    if saved["first"]:
        ctx.stats.collected += 1
        outcome = "collected"
    elif saved["changed"]:
        ctx.stats.updated += 1
        outcome = "updated"
    else:
        ctx.stats.unchanged += 1
        outcome = "unchanged"
    ctx.event("info", "article_" + outcome, article_id=article["id"], title=(parsed.title or "")[:80],
              media=len(parsed.media), comments=len(parsed.comments), warnings=parsed.warnings or None)
    return outcome
