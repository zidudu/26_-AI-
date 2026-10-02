"""FastAPI 앱: 개발용 HTML GUI + JSON API.

GUI는 기능 확인용 최소 구성(표·폼·버튼)입니다. Figma 디자인이 제공되면 그것을 기준으로 다시 구현합니다.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, StrictBool

from .. import VERSION
from ..common import AppError, now_iso, to_kst_text
from ..config import Settings, load_settings, save_settings
from ..db import ARTICLE_STATES, CHANNEL_EDITABLE, MEDIA_STATES, Database
from ..storage import load_raw_html

HERE = Path(__file__).resolve().parent


class BookmarkUpdate(BaseModel):
    saved: StrictBool


def create_app(settings: Settings | None = None, db: Database | None = None, service=None) -> FastAPI:
    settings = settings or load_settings()
    db = db or Database(settings.db_path)
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if service:
            service.start()
        try:
            yield
        finally:
            if service:
                service.shutdown()

    app = FastAPI(title="arca-archive", version=VERSION, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["kst"] = to_kst_text
    templates.env.filters["kst_short"] = lambda v: to_kst_text(v, "%m-%d %H:%M")
    templates.env.filters["tojson_ko"] = lambda v: json.dumps(v, ensure_ascii=False, indent=2, default=str)
    templates.env.filters["fileext"] = _file_ext
    from .reader import display_kind, render_body
    templates.env.filters["displaykind"] = display_kind
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    @app.middleware('http')
    async def media_response_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith('/media-files/') or (path.startswith('/media/') and path.endswith('/file')):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
        return response

    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    settings.media_path.mkdir(parents=True, exist_ok=True)
    app.mount("/media-files", StaticFiles(directory=str(settings.media_path)), name="media-files")

    def render(request: Request, name: str, **context) -> HTMLResponse:
        from ..sites import all_sites, get_site

        status = service.status() if service else {"running": False, "current": None, "queue": [], "session": {},
                                                    "scheduler_enabled": False, "login_window_open": False, "sessions": {}}
        base = {"request": request, "settings": settings, "version": VERSION, "status": status,
                "channels": db.list_channels(), "nav": name.split(".")[0], "sites": all_sites()}
        base['site_labels'] = {s.key:s.label for s in all_sites()}
        base['channel_urls'] = {c['id']:get_site(c.get('site')).channel_url(c) for c in base['channels']}
        base.update(context)
        return templates.TemplateResponse(request, name, base)

    # ------------------------------------------------------------------ 상태
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        channels = db.list_channels()
        for c in channels:
            c["counts"] = db.count_articles_by_state(c["id"])
            c["media_counts"] = db.count_media_by_state(c["id"])
            c["media_mb"] = round(db.media_bytes(c["id"]) / 1e6, 1)
        runs = db.list_runs(10)
        errors = db.failed_items(10)
        return render(request, "index.html", channel_rows=channels, runs=runs, errors=errors,
                      totals={"articles": db.count_articles_by_state(), "media": db.count_media_by_state(),
                              "media_mb": round(db.media_bytes() / 1e6, 1),
                              "db_mb": round(settings.db_path.stat().st_size / 1e6, 1) if settings.db_path.exists() else 0})

    @app.get("/api/status")
    def api_status():
        if not service:
            return {"running": False}
        return service.status()

    # ------------------------------------------------------------------ 채널
    @app.get("/channels", response_class=HTMLResponse)
    def channels_page(request: Request):
        channels = db.list_channels()
        for c in channels:
            c["counts"] = db.count_articles_by_state(c["id"])
        return render(request, "channels.html", channel_rows=channels, message=request.query_params.get("m"))

    @app.post("/channels")
    def channel_add(slug: str = Form(...), fetch_mode: str = Form("auto"), interval_minutes: str = Form("60"),
                    site: str = Form("arca")):
        from urllib.parse import quote

        from ..sites import get_site

        try:
            adapter = get_site(site)
            info = adapter.normalize_channel_input(slug)
        except ValueError as exc:
            return RedirectResponse("/channels?m=" + quote(f"등록 실패: {exc}"), status_code=303)
        except Exception as exc:  # noqa: BLE001 - 네트워크 조회 실패 등
            return RedirectResponse("/channels?m=" + quote(f"채널 정보를 확인하지 못했습니다: {type(exc).__name__}"), status_code=303)
        new_slug = info["slug"]
        existing = db.get_channel_by_slug(new_slug)
        if existing and existing.get("site") != adapter.key:
            new_slug = f"{info['slug']}@{adapter.key}"
            existing = db.get_channel_by_slug(new_slug)
        if existing:
            return RedirectResponse("/channels?m=이미+등록된+채널입니다", status_code=303)
        interval = _opt_int(interval_minutes) or settings.crawl.default_interval_minutes
        if fetch_mode not in ("auto", "http", "browser"):
            fetch_mode = "auto"
        db.create_channel(new_slug, site=adapter.key, site_channel_id=info["site_channel_id"], name=info.get("name"),
                          category=info.get("category"), fetch_mode=fetch_mode, interval_minutes=max(1, interval),
                          initial_pages=settings.crawl.default_initial_pages,
                          max_pages_per_run=settings.crawl.default_max_pages_per_run,
                          recheck_days=settings.crawl.recheck_days)
        return RedirectResponse("/channels?m=" + quote(f"등록했습니다: {adapter.label} {info.get('name') or new_slug}"), status_code=303)

    @app.get("/channels/{channel_id}", response_class=HTMLResponse)
    def channel_edit(request: Request, channel_id: int):
        channel = db.get_channel(channel_id)
        if not channel:
            raise HTTPException(404)
        return render(request, "channel_edit.html", channel=channel, counts=db.count_articles_by_state(channel_id),
                      runs=db.list_runs(10, channel_id), message=request.query_params.get("m"))

    @app.post("/channels/{channel_id}")
    async def channel_save(request: Request, channel_id: int):
        channel = db.get_channel(channel_id)
        if not channel:
            raise HTTPException(404)
        form = await request.form()
        fields: dict = {}
        for key in ("name", "fetch_mode", "category"):
            if key in form:
                fields[key] = (form.get(key) or "").strip() or None
        for key in ("interval_minutes", "initial_pages", "max_pages_per_run", "recheck_days"):
            if key in form and str(form.get(key)).strip():
                try:
                    fields[key] = max(0, int(form.get(key)))
                except ValueError:
                    pass
        for key in ("enabled", "collect_media", "collect_comments", "include_notices"):
            fields[key] = 1 if form.get(key) in ("on", "1", "true") else 0
        if fields.get("fetch_mode") not in ("auto", "http", "browser"):
            fields["fetch_mode"] = "auto"
        if form.get("reset_browser_flag") == "on":
            db.update_channel(channel_id, requires_browser=0)
        db.update_channel(channel_id, **{k: v for k, v in fields.items() if k in CHANNEL_EDITABLE})
        return RedirectResponse(f"/channels/{channel_id}?m=저장했습니다", status_code=303)

    @app.post("/channels/{channel_id}/backfill")
    def channel_backfill(channel_id: int, request: Request, pages: str = Form("10")):
        """다음 실행에서 목록을 N페이지까지 훑어 과거 글을 등록합니다(아는 글이 나와도 계속)."""
        channel = db.get_channel(channel_id)
        if not channel:
            raise HTTPException(404)
        n = _opt_int(pages) or 0
        if not 1 <= n <= 500:
            return _redirect_back(request, "페이지 수는 1~500 사이로 입력하세요")
        db.update_channel(channel_id, backfill_pages=n)
        if service:
            service.request_run(channel_id, "manual")
        return _redirect_back(request, f"{channel['slug']} 과거 글 {n}페이지 가져오기를 예약했습니다")

    @app.post("/channels/{channel_id}/toggle")
    def channel_toggle(channel_id: int, request: Request):
        """자동(스케줄) 실행 일시중지/재개. 수동 '지금 실행'과 진행 중인 실행에는 영향이 없습니다."""
        channel = db.get_channel(channel_id)
        if not channel:
            raise HTTPException(404)
        enable = not channel["enabled"]
        fields = {"enabled": 1 if enable else 0}
        if enable and not channel.get("next_run_at"):
            fields["next_run_at"] = now_iso()
        db.update_channel(channel_id, **fields)
        if service and not enable:
            service.drop_scheduled(channel_id)
        return _redirect_back(request, f"{channel['slug']} 자동 실행을 " + ("재개했습니다" if enable else "일시중지했습니다. 수동 실행은 계속 가능합니다"))

    @app.post("/channels/{channel_id}/delete")
    def channel_delete(channel_id: int):
        db.delete_channel(channel_id)
        return RedirectResponse("/channels?m=삭제했습니다", status_code=303)

    @app.post("/channels/{channel_id}/run")
    def channel_run(channel_id: int, request: Request):
        if not service:
            raise HTTPException(503, "서비스가 없습니다")
        result = service.request_run(channel_id, "manual")
        return _redirect_back(request, "실행을 요청했습니다" if result.get("ok") else result.get("reason", "실패"))

    @app.post("/api/run/{channel_id}")
    def api_run(channel_id: int):
        if not service:
            raise HTTPException(503, "서비스가 없습니다")
        return service.request_run(channel_id, "manual")

    @app.post("/stop")
    def stop(request: Request):
        result = service.stop_current() if service else {"message": "서비스가 없습니다"}
        return _redirect_back(request, result.get("message", "중지를 요청했습니다"))

    @app.post("/api/stop")
    def api_stop():
        if not service:
            raise HTTPException(503, "서비스가 없습니다")
        return service.stop_current()

    @app.post("/scheduler")
    def scheduler_toggle(enabled: str = Form("off")):
        if service:
            service.scheduler_enabled = enabled == "on"
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ 실행 기록
    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request, channel_id: str | None = None):
        return render(request, "runs.html", runs=db.list_runs(100, _opt_int(channel_id)))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: int, level: str | None = None):
        run = db.get_run(run_id)
        if not run:
            raise HTTPException(404)
        events = db.list_events(run_id, 1000, level or None)
        return render(request, "run_detail.html", run=run, events=events, level=level or "")

    @app.get("/api/runs/{run_id}/events")
    def api_events(run_id: int, after: int = 0, limit: int = 200):
        return {"run": db.get_run(run_id), "events": db.list_events(run_id, limit, None, after)}

    # ------------------------------------------------------------------ 게시글
    def article_bookmarks(rows):
        saved = db.bookmarked_ids("article", (a["id"] for a in rows))
        for a in rows:
            a["bookmarked"] = a["id"] in saved

    def media_bookmarks(rows):
        media_saved = db.bookmarked_ids("media", (m["id"] for m in rows))
        article_saved = db.bookmarked_ids("article", (m["article_id"] for m in rows))
        for m in rows:
            m["media_bookmarked"] = m["id"] in media_saved
            m["article_bookmarked"] = m["article_id"] in article_saved

    @app.get("/articles", response_class=HTMLResponse)
    def articles_page(request: Request, channel_id: str | None = None, state: str | None = None, q: str | None = None,
                      page: str | None = None, site: str = '', scope: str = 'all', date_from: str = '', date_to: str = '', media_state: str = ''):
        per_page = 50
        cid = _opt_int(channel_id)
        state = (state or "").strip()
        q = (q or "").strip()
        scope = scope if scope in ('all','title','body','author','comments') else 'all'
        from datetime import date
        for value in (date_from, date_to):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise HTTPException(400, '날짜 형식은 YYYY-MM-DD입니다.')
        filters = dict(site=site, scope=scope, date_from=date_from, date_to=date_to, media_state=media_state)
        total = db.list_articles(cid, state or None, q or None, 1, 0, **filters)[1]
        pager = _Pager("/articles", {"channel_id": cid, "state": state, "q": q, **filters}, total, per_page, _opt_int(page) or 1)
        rows, _ = db.list_articles(cid, state or None, q or None, per_page, pager.offset, **filters)
        article_bookmarks(rows)
        return render(request, "articles.html", rows=rows, total=total, pager=pager,
                      channel_id=cid, state=state, q=q, states=ARTICLE_STATES, **filters)

    @app.get("/articles/{article_id}", response_class=HTMLResponse)
    def article_detail(request: Request, article_id: int):
        article = db.get_article(article_id)
        if not article:
            raise HTTPException(404)
        article_bookmarks([article])
        media = db.list_media(article_id)
        media_bookmarks(media)
        for m in media:
            m["local_url"] = _local_media_url(settings, m)
        return render(request, "article_detail.html", article=article, media=media, comments=db.list_comments(article_id),
                      comment_fetch=db.comment_fetch(article_id),
                      revisions=db.list_revisions(article_id), channel=db.get_channel(article["channel_id"]),
                      badges=json.loads(article.get("badges") or "[]"), reader_html=render_body(article.get('body_html'), article['url'], media))

    @app.get("/articles/{article_id}/raw", response_class=HTMLResponse)
    def article_raw(article_id: int):
        article = db.get_article(article_id)
        if not article or not article.get("raw_html_path") or not Path(article["raw_html_path"]).exists():
            raise HTTPException(404, "원본 HTML이 없습니다")
        html = load_raw_html(Path(article["raw_html_path"]))
        # 원본은 그대로 보관하지만 화면에서는 스크립트가 실행되지 않도록 텍스트로 보여줍니다.
        return HTMLResponse(f"<pre style='white-space:pre-wrap;font:12px/1.4 monospace'>{_escape(html)}</pre>")

    @app.post("/articles/{article_id}/requeue")
    def article_requeue(article_id: int):
        db.update_article(article_id, state="discovered", attempts=0, next_retry_at=None, state_code=None, state_message=None)
        db.requeue_media(article_id)
        db.requeue_comments(article_id)
        return RedirectResponse(f"/articles/{article_id}", status_code=303)

    # ------------------------------------------------------------------ 미디어
    @app.post('/articles/{article_id}/comments/retry')
    def comments_retry(request: Request, article_id: int):
        if not db.get_article(article_id):
            raise HTTPException(404)
        changed = db.requeue_comments(article_id)
        return _redirect_back(request, '다음 채널 수집에서 댓글을 다시 확인합니다.' if changed else '재시도할 미완료 댓글이 없습니다.')

    @app.get("/media", response_class=HTMLResponse)
    def media_page(request: Request, channel_id: str | None = None, kind: str | None = None, state: str | None = None,
                   page: str | None = None):
        per_page = 60
        cid = _opt_int(channel_id)
        kind = (kind or "").strip()
        state = (state or "").strip()
        total = db.list_media_page(cid, kind or None, state or None, 1, 0)[1]
        pager = _Pager("/media", {"channel_id": cid, "kind": kind, "state": state}, total, per_page, _opt_int(page) or 1)
        rows, _ = db.list_media_page(cid, kind or None, state or None, per_page, pager.offset)
        media_bookmarks(rows)
        for m in rows:
            m["local_url"] = _local_media_url(settings, m)
        return render(request, "media.html", rows=rows, total=total, pager=pager,
                      channel_id=cid, kind=kind, state=state, states=MEDIA_STATES,
                      counts=db.count_media_by_state(cid))

    @app.get("/media/{media_id}/file")
    def media_file(media_id: int):
        m = db.get_media(media_id)
        if not m or not m.get("file_path") or not Path(m["file_path"]).exists():
            raise HTTPException(404)
        return FileResponse(m["file_path"], media_type=m.get("content_type") or None)

    # ------------------------------------------------------------------ 북마크
    @app.put("/api/bookmarks/{kind}/{target_id}")
    def bookmark_save(kind: Literal["article", "media"], target_id: int, body: BookmarkUpdate):
        try:
            db.set_bookmark(kind, target_id, body.saved)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"kind": kind, "target_id": target_id, "saved": body.saved}

    @app.get("/bookmarks", response_class=HTMLResponse)
    def bookmarks_page(request: Request, kind: str = "", channel_id: str = "", media_kind: str = "",
                       q: str = "", sort: str = "newest", page: str = "1"):
        kind = kind if kind in ("article", "media") else ""
        media_kind = media_kind if media_kind in ("image", "gif", "video", "emoticon", "external") else ""
        sort = "oldest" if sort == "oldest" else "newest"
        cid, q = _opt_int(channel_id), q.strip()
        filters = dict(kind=kind, channel_id=cid, media_kind=media_kind, query=q, sort=sort)
        total = db.list_bookmarks(**filters, limit=0)[1]
        pager = _Pager("/bookmarks", {"kind": kind, "channel_id": cid, "media_kind": media_kind, "q": q, "sort": sort},
                       total, 60, _opt_int(page) or 1)
        rows, _ = db.list_bookmarks(**filters, limit=60, offset=pager.offset)
        for row in rows:
            row["local_url"] = (_local_media_url(settings, dict(row, id=row["media_id"]))
                                if row["file_path"] and Path(row["file_path"]).is_file() else None)
        return render(request, "bookmarks.html", rows=rows, total=total, pager=pager,
                      kind=kind, channel_id=cid, media_kind=media_kind, q=q, sort=sort)

    # ------------------------------------------------------------------ 오류
    @app.get('/backlog', response_class=HTMLResponse)
    def backlog_page(request: Request, channel_id: str = '', reason: str = '', page: str = '1'):
        from ..backlog import inspect_backlog, REASONS
        cid = _opt_int(channel_id)
        report = inspect_backlog(db, cid, reason, 0)
        pager = _Pager('/backlog', {'channel_id': cid, 'reason': reason}, report['total'], 60, _opt_int(page) or 1)
        report = inspect_backlog(db, cid, reason, 60, pager.offset)
        return render(request, 'backlog.html', report=report, reasons=REASONS, channel_id=cid, reason=reason, pager=pager)

    @app.get('/api/backlog')
    def backlog_api(channel_id: int | None = None):
        from ..backlog import inspect_backlog
        return inspect_backlog(db, channel_id)

    @app.post('/channels/{channel_id}/backlog')
    def backlog_run(request: Request, channel_id: int):
        if not service:
            raise HTTPException(503, '서비스가 없습니다')
        result = service.request_run(channel_id, 'backlog_manual')
        return _redirect_back(request, '미디어 대기 처리를 예약했습니다' if result.get('ok') else result.get('reason', '실패'))

    @app.get("/errors", response_class=HTMLResponse)
    def errors_page(request: Request):
        return render(request, "errors.html", failed=db.failed_items(200), events=db.recent_error_events(100),
                      comment_failures=db.incomplete_comments(200),
                      message=request.query_params.get("m"))

    @app.post("/errors/requeue")
    def errors_requeue(target: str = Form("articles")):
        if target == "media":
            n = db.requeue_media()
        else:
            n = db.requeue_articles()
        return RedirectResponse(f"/errors?m={n}개를+다시+대기열에+넣었습니다", status_code=303)

    # ------------------------------------------------------------------ 설정
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return render(request, "settings.html", settings_json=json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
                      message=request.query_params.get("m"))

    @app.post("/settings")
    async def settings_save(request: Request):
        form = await request.form()
        raw = form.get("settings_json") or ""
        try:
            data = json.loads(raw)
            new_settings = Settings.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - 사용자 입력 검증 결과를 그대로 알립니다.
            return RedirectResponse(f"/settings?m=저장+실패:+{type(exc).__name__}", status_code=303)
        new_settings._config_path = settings.config_path
        save_settings(new_settings)
        # 실행 중 반영 가능한 항목만 즉시 갱신합니다(서버/경로는 재시작 필요).
        for key in ("http", "browser", "media", "retry", "crawl", "scheduler_enabled", "seed_channels"):
            setattr(settings, key, getattr(new_settings, key))
        if service:
            service.scheduler_enabled = settings.scheduler_enabled
        return RedirectResponse("/settings?m=저장했습니다.+서버·경로+항목은+재시작+후+적용됩니다", status_code=303)

    # ------------------------------------------------------------------ 브라우저 세션
    @app.get("/session", response_class=HTMLResponse)
    def session_page(request: Request):
        return render(request, "session.html", message=request.query_params.get("m"))

    @app.post("/session/login")
    def session_login(start_url: str = Form(""), site: str = Form("arca")):
        from ..sites import get_site

        if not service:
            raise HTTPException(503)
        url = (start_url or "").strip() or get_site(site).login_url
        result = service.open_login_window(url)
        return RedirectResponse("/session?m=" + (result.get("message") or result.get("reason", "")), status_code=303)

    @app.post("/session/check")
    def session_check(site: str = Form("arca")):
        if not service:
            raise HTTPException(503)
        result = service.check_session(site)
        text = result.get("reason") or result.get("error") or (
            "로그인됨" if result.get("logged_in") else ("로그인되지 않음" if result.get("logged_in") is False else "판단 불가"))
        return RedirectResponse("/session?m=" + text, status_code=303)

    # ------------------------------------------------------------------ 내보내기 / 백업
    @app.get("/api/export/articles.jsonl")
    def api_export(channel_id: str | None = None, body_html: str | None = None):
        from fastapi.responses import StreamingResponse

        from ..export import iter_articles

        cid = _opt_int(channel_id)

        def gen():
            for record in iter_articles(db, cid, include_body_html=body_html in ("1", "true", "on")):
                yield json.dumps(record, ensure_ascii=False) + "\n"

        name = f"articles-{cid or 'all'}.jsonl"
        return StreamingResponse(gen(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/backup")
    def backup(request: Request):
        from datetime import datetime

        path = db.backup_to(settings.data_path / "backups" / f"arca-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3")
        return _redirect_back(request, f"DB 백업 완료: {path.name} ({path.stat().st_size/1e6:.1f} MB). 미디어 폴더는 별도로 복사하세요")

    @app.exception_handler(AppError)
    def _app_error(request: Request, exc: AppError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=400)

    return app


def _file_ext(m: dict) -> str:
    """미디어 항목의 표시용 확장자: 저장 파일 → content-type → 원본 경로 순."""
    path = m.get("file_path") if isinstance(m, dict) else None
    if path:
        suffix = Path(path).suffix.lower().lstrip(".")
        if suffix:
            return suffix
    from ..storage import EXT_BY_CONTENT_TYPE

    ctype = (m.get("content_type") or "").split(";")[0].strip().lower() if isinstance(m, dict) else ""
    if ctype in EXT_BY_CONTENT_TYPE:
        return EXT_BY_CONTENT_TYPE[ctype].lstrip(".")
    key = m.get("source_key") or "" if isinstance(m, dict) else ""
    return Path(key).suffix.lower().lstrip(".")


def _opt_int(value) -> int | None:
    """폼/링크의 빈 값('')과 잘못된 값은 None으로 취급합니다. 422 대신 '전체'로 동작합니다."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class _Pager:
    """목록 페이지 이동 링크. 빈 필터는 주소에 넣지 않고, 페이지 번호는 범위 안으로 맞춥니다."""

    def __init__(self, path: str, params: dict, total: int, per_page: int, page: int):
        from urllib.parse import urlencode

        self._urlencode = urlencode
        self.path = path
        self.params = {k: v for k, v in params.items() if v not in (None, "")}
        self.total = total
        self.per_page = per_page
        self.pages = max(1, math.ceil(total / per_page)) if total else 1
        self.page = min(max(1, page), self.pages)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    def url(self, page: int) -> str:
        query = dict(self.params)
        if page > 1:
            query["page"] = page
        return self.path + ("?" + self._urlencode(query) if query else "")

    @property
    def prev(self):
        return self.page - 1 if self.page > 1 else None

    @property
    def next(self):
        return self.page + 1 if self.page < self.pages else None

    def numbers(self, around: int = 2) -> list:
        """1 … 5 6 [7] 8 9 … 15 형태. None 은 생략 표시."""
        wanted = {1, self.pages} | {n for n in range(self.page - around, self.page + around + 1) if 1 <= n <= self.pages}
        result: list = []
        last = 0
        for n in sorted(wanted):
            if n - last > 1:
                result.append(None)
            result.append(n)
            last = n
        return result


def _redirect_back(request: Request, message: str, default: str = "/") -> RedirectResponse:
    """Referer 페이지로 돌아가되 경로만 사용하고, 기존 m 파라미터는 새 메시지로 교체합니다."""
    from urllib.parse import parse_qsl, urlencode, urlsplit

    parts = urlsplit(request.headers.get("referer") or "")
    path = parts.path if parts.path.startswith("/") else default
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "m"]
    query.append(("m", message))
    return RedirectResponse(f"{path}?{urlencode(query)}", status_code=303)


def _local_media_url(settings: Settings, m: dict) -> str | None:
    path = m.get("file_path")
    if not path:
        return None
    try:
        rel = Path(path).resolve().relative_to(settings.media_path.resolve())
    except ValueError:
        return f"/media/{m['id']}/file"
    return "/media-files/" + "/".join(rel.parts)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
