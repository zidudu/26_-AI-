"""재확인 단계: 최근 글의 수정/삭제 감지와 만료된 미디어 URL 갱신."""
from __future__ import annotations

from datetime import timedelta

from ..common import utcnow
from .collect import collect_article
from .media import MAX_UPGRADE_ATTEMPTS, download_media_for_article, originals_allowed
from .runner import CrawlContext


def recheck(ctx: CrawlContext, backlog_only: bool = False) -> dict:
    settings = ctx.settings
    channel = ctx.channel
    db = ctx.db
    budget = settings.crawl.max_rechecks_per_run
    handled = 0
    seen: set[int] = set(ctx.touched_articles)  # 이번 실행에서 이미 연 글은 다시 열지 않습니다.

    # 댓글 미완료는 글 작성일과 무관하게 재시도합니다. 미디어 처리 몫도 남겨 둡니다.
    if not backlog_only and channel.get('collect_comments', 1) and budget > 0:
        comment_budget = max(1, budget // 5)
        for article in db.comment_retry_candidates(channel['id'], comment_budget + len(seen)):
            ctx.check_stop()
            if ctx.media_budget_exhausted():
                break
            if article['id'] in seen:
                continue
            seen.add(article['id'])
            ctx.event('info', 'comments_retry', article_id=article['id'])
            outcome = collect_article(ctx, article, recheck=True)
            if outcome in ('collected', 'updated', 'unchanged') and channel.get('collect_media', 1):
                download_media_for_article(ctx, db.get_article(article['id']))
            handled += 1
            ctx.checkpoint()
            if handled >= comment_budget:
                break

    # 1) 만료/거부된 미디어가 있는 글: 글을 다시 열어 새 서명 URL을 받고 다운로드합니다.
    if channel.get("collect_media", 1) and handled < budget:
        for article in db.expired_media_articles(channel["id"], budget + len(seen), MAX_UPGRADE_ATTEMPTS,
                                                 include_upgrades=not backlog_only and settings.media.request_original and originals_allowed(db)):
            ctx.check_stop()
            if ctx.media_budget_exhausted():
                ctx.event("info", "recheck_deferred", reason="TIME_BUDGET", handled=handled)
                break
            if article["id"] in seen:
                continue
            seen.add(article["id"])
            outcome = collect_article(ctx, article, recheck=True)
            if outcome in ("collected", "updated", "unchanged"):
                download_media_for_article(ctx, db.get_article(article["id"]))
            handled += 1
            ctx.checkpoint()
            if handled >= budget:
                break

    # 2) 최근 N일 내 글 중 마지막 확인이 오래된 글: 수정/삭제 감지.
    recheck_days = int(channel.get("recheck_days") if channel.get("recheck_days") is not None else settings.crawl.recheck_days)
    if not backlog_only and recheck_days > 0 and handled < budget and not ctx.media_budget_exhausted():
        since = (utcnow() - timedelta(days=recheck_days)).replace(microsecond=0).isoformat()
        before = (utcnow() - timedelta(hours=settings.crawl.recheck_interval_hours)).replace(microsecond=0).isoformat()
        for article in db.recheck_candidates(channel["id"], since, before, budget - handled + len(seen)):
            ctx.check_stop()
            if article["id"] in seen:
                continue
            seen.add(article["id"])
            outcome = collect_article(ctx, article, recheck=True)
            if outcome in ("collected", "updated", "unchanged") and channel.get("collect_media", 1):
                download_media_for_article(ctx, db.get_article(article["id"]))
            handled += 1
            ctx.checkpoint()
            if ctx.media_budget_exhausted():
                break
    ctx.event("info", "recheck_done", handled=handled, budget=budget)
    return {"handled": handled}
