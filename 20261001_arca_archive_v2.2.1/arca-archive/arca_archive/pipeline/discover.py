"""목록 페이지에서 신규 글을 발견합니다(증분).

- 첫 실행(last_max_article_id 없음): initial_pages 만큼만 읽습니다.
- 이후: 1페이지부터 읽되, 한 페이지의 일반 글이 모두 이미 아는 글이면 멈춥니다.
"""
from __future__ import annotations

from ..common import AppError
from ..sites.base import ArticleParseError
from .runner import CrawlContext


def discover(ctx: CrawlContext) -> dict:
    channel = ctx.channel
    db = ctx.db
    slug = channel["slug"]
    initial = channel.get("last_max_article_id") is None
    backfill = int(channel.get("backfill_pages") or 0)
    if backfill > 0:
        # 백필: 아는 글이 나와도 멈추지 않고 요청한 페이지 수만큼 훑습니다. 새 글 등록만 하며 수집은 평소 흐름을 따릅니다.
        max_pages = backfill
    else:
        max_pages = int(channel.get("initial_pages") or 1) if initial else int(channel.get("max_pages_per_run") or 1)
    include_notices = bool(channel.get("include_notices", 1))
    max_seen = int(channel.get("last_max_article_id") or 0)
    reason = "PAGE_LIMIT"
    pages = 0
    total_new = 0
    site = ctx.site
    for number in range(1, max_pages + 1):
        request = site.list_request(channel, number)
        result, state = ctx.fetch_checked(request)
        if state.kind != "ok":
            if state.stop:
                raise AppError(state.code, state.message, stop=True)
            ctx.event("warning", "list_page_error", page=number, code=state.code, message=state.message)
            ctx.stats.warnings.append(state.code)
            reason = state.code
            ctx.stats.coverage_complete = False
            break
        try:
            page = site.parse_list(result, channel)
        except ArticleParseError as exc:
            raise AppError("LIST_" + exc.code, exc.message, stop=True) from None
        if page.hidden_for_anonymous:
            if ctx.fetcher_name == "http" and ctx.escalate_to_browser("LIST_HIDDEN"):
                result, state = ctx.fetch_checked(request)
                if state.kind != "ok":
                    raise AppError(state.code, state.message, stop=True)
                page = site.parse_list(result, channel)
            if page.hidden_for_anonymous:
                raise AppError("LOGIN_REQUIRED",
                               "이 채널의 글은 로그인 세션에서만 보입니다. 로그인 브라우저를 열어 로그인한 뒤 다시 실행하세요.",
                               stop=True)
        if ctx.fetcher_name == "browser" and page.logged_in is not None:
            ctx.stats.logged_in = page.logged_in
        if page.channel_name and page.channel_name != channel.get("name"):
            db.update_channel(channel["id"], name=page.channel_name)
            channel["name"] = page.channel_name
        rows = [r for r in page.rows if include_notices or not r.is_notice]
        pages += 1
        ctx.stats.pages_scanned = pages
        if not rows:
            reason = "EMPTY_PAGE"
            ctx.event("info", "list_page", page=number, rows=0, new=0)
            break
        records = []
        for r in rows:
            record = {**r.__dict__, "id": site.internal_article_id(channel, r.remote_id)}
            records.append(record)
        normal_ids = [rec["id"] for rec in records if not rec["is_notice"]]
        known = db.known_article_ids(channel["id"], [rec["id"] for rec in records])
        new_ids = db.upsert_discovered(channel["id"], records, ctx.run_id)
        total_new += len(new_ids)
        ctx.stats.discovered_new = total_new
        if normal_ids:
            max_seen = max(max_seen, max(int(rec["remote_id"]) for rec in records if not rec["is_notice"]))
        ctx.event("info", "list_page", page=number, rows=len(rows), new=len(new_ids), known=len(known))
        if not initial and not backfill and normal_ids and all(i in known for i in normal_ids):
            reason = "REACHED_KNOWN"
            break
        if not page.has_next:
            reason = "LAST_PAGE"
            break
    else:
        reason = "PAGE_LIMIT"
    if backfill > 0:
        reason = "BACKFILL_DONE" if reason in ("PAGE_LIMIT", "LAST_PAGE", "EMPTY_PAGE") else reason
        db.update_channel(channel["id"], backfill_pages=0)
        channel["backfill_pages"] = 0
        ctx.event("info", "backfill_done", pages=pages, new=total_new)
    elif reason == "PAGE_LIMIT" and not initial:
        ctx.stats.coverage_complete = False
        ctx.stats.warnings.append("LIST_PAGE_LIMIT")
    ctx.stats.discover_reason = reason
    if max_seen:
        db.update_channel(channel["id"], last_max_article_id=max_seen)
        channel["last_max_article_id"] = max_seen
    ctx.event("info", "discover_done", pages=pages, new=total_new, reason=reason, initial=initial)
    return {"pages": pages, "new": total_new, "reason": reason, "initial": initial}
