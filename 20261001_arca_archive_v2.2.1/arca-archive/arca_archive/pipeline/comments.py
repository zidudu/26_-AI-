"""페이지별 댓글 수집. 본문 상태와 분리하여 보관 수·미완료 사유·재시도를 추적합니다."""
from __future__ import annotations

from ..common import AppError, iso_after, now_iso
from ..sites.base import ArticleParseError
from ..storage import save_raw_text
from .retry import schedule_retry
from .runner import CrawlContext, StopRequested


def collect_comment_pages(ctx: CrawlContext, article: dict, result, expected: int) -> None:
    db, site, channel = ctx.db, ctx.site, ctx.channel
    aid = article['id']
    previous = db.comment_fetch(aid) or {}
    attempts = previous.get('attempts', 0)
    saved_ids: set[str] = set()
    total, invalid = expected, 0
    reason = None
    retry_at = iso_after(ctx.settings.retry.base_delay_minutes * 60)
    db.save_comment_fetch(aid, 'pending', total, 0, attempts, 'IN_PROGRESS', retry_at)
    try:
        for page in range(1, ctx.settings.crawl.max_comment_pages_per_article + 1):
            ctx.check_stop()
            if ctx.media_budget_exhausted():
                reason = 'TIME_BUDGET'
                break
            response, state = ctx.fetch_checked(site.comments_request(result, article, channel, page))
            if state.kind != 'ok':
                raise AppError(state.code, state.message, stop=state.stop)
            parsed = site.parse_comments(response)
            total = parsed.total
            invalid += parsed.invalid_rows
            if ctx.settings.crawl.keep_raw_html:
                try:
                    save_raw_text(ctx.settings, channel['slug'], aid, now_iso()+f'-comments-{page}', response.html, 'json')
                except OSError as exc:
                    ctx.event('warning', 'raw_comments_save_failed', article_id=aid, code=type(exc).__name__)
            rows = {}
            for c in parsed.rows:
                try:
                    rows[c['remote_id']] = {
                        **c, 'id': site.internal_comment_id(channel, c['remote_id']),
                        'parent_id': site.internal_comment_id(channel, c['parent_remote_id']) if c.get('parent_remote_id') else None,
                    }
                except (ValueError, TypeError, KeyError):
                    invalid += 1
            # 저장 실패는 실행 오류로 전파합니다. 저장하지 못한 행을 완료 수에 넣지 않습니다.
            db.replace_comments(aid, list(rows.values()))
            new_ids = rows.keys() - saved_ids
            ctx.stats.comments_saved += len(new_ids)
            saved_ids.update(rows)
            db.save_comment_fetch(aid, 'pending', total, len(saved_ids), attempts, 'IN_PROGRESS', retry_at)
            if len(saved_ids) >= total:
                break
            if not new_ids:
                reason = 'NO_PROGRESS'
                break
        else:
            reason = 'PAGE_LIMIT'
    except StopRequested:
        db.save_comment_fetch(aid, 'pending', total, len(saved_ids), attempts, 'INTERRUPTED', retry_at)
        raise
    except (AppError, ArticleParseError) as exc:
        reason = exc.code
        if isinstance(exc, AppError) and exc.stop:
            retry_state, retry_at = schedule_retry(attempts + 1, ctx.settings.retry)
            db.save_comment_fetch(aid, 'error' if retry_state == 'error' else 'incomplete',
                                  total, len(saved_ids), attempts + 1, reason, retry_at)
            ctx.stats.comments_incomplete += 1
            raise
    if invalid:
        ctx.event('warning', 'comments_invalid_rows', article_id=aid, count=invalid)
        reason = reason or 'INVALID_ROWS'
    if reason or len(saved_ids) < total:
        reason = reason or 'COUNT_MISMATCH'
        deferred = reason == 'TIME_BUDGET'
        attempts += int(not deferred)
        retry_state, retry_at = schedule_retry(attempts, ctx.settings.retry)
        state = 'pending' if deferred else ('error' if retry_state == 'error' else 'incomplete')
        db.save_comment_fetch(aid, state, total, len(saved_ids), attempts, reason, retry_at)
        ctx.stats.comments_incomplete += 1
        ctx.event('warning', 'comments_incomplete', article_id=aid, code=reason,
                  saved=len(saved_ids), expected=total, next_retry_at=retry_at)
    else:
        db.save_comment_fetch(aid, 'complete', total, len(saved_ids))
        ctx.event('info', 'comments_collected', article_id=aid, count=len(saved_ids))
