"""명령행 진입점: serve / run / login / check-session / add-channel / channels / stats"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from . import VERSION
from .common import AppError
from .config import DEFAULT_CONFIG_PATH, Settings, load_settings
from .db import Database


def setup_logging(settings: Settings, console: bool = True) -> None:
    settings.log_path.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(settings.log_path / "app.log", maxBytes=5_000_000,
                                                        backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        root.addHandler(stream)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 요청 URL(서명 포함)을 로그에 남기지 않습니다.
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def open_db(settings: Settings) -> Database:
    db = Database(settings.db_path)
    for slug in settings.seed_channels:
        if not db.get_channel_by_slug(slug):
            db.create_channel(slug, interval_minutes=settings.crawl.default_interval_minutes,
                              initial_pages=settings.crawl.default_initial_pages,
                              max_pages_per_run=settings.crawl.default_max_pages_per_run,
                              recheck_days=settings.crawl.recheck_days,
                              collect_comments=1 if settings.crawl.collect_comments else 0)
    return db


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from .service import CrawlerService
    from .web.app import create_app

    host = args.host or settings.server.host
    port = args.port or settings.server.port
    running = existing_server_status(host, port)
    if running is not None:
        print(f"[arca-archive] 서버가 이미 http://{host}:{port}/ 에서 실행 중입니다. 새로 시작하지 않습니다.")
        if running.get("running") and running.get("current"):
            print(f"  현재 수집 중: {running['current'].get('channel_slug')} (run #{running['current'].get('run_id')})")
        print("  기존 서버를 종료하려면 그 서버의 콘솔 창에서 Ctrl+C 를 누르세요.")
        return 0
    db = open_db(settings)
    service = CrawlerService(settings, db)
    app = create_app(settings, db, service)
    print(f"[arca-archive {VERSION}] GUI: http://{host}:{port}/  (Ctrl+C 로 종료)")
    import faulthandler
    import os
    log = logging.getLogger('arca.server')
    with (settings.log_path / 'faults.log').open('a', encoding='utf-8') as fault_log:
        faulthandler.enable(file=fault_log, all_threads=True)
        log.info('server_started pid=%d version=%s', os.getpid(), VERSION)
        try:
            uvicorn.run(app, host=host, port=port, log_level="info", log_config=None, access_log=False)
        finally:
            log.info('server_stopped pid=%d', os.getpid())
            faulthandler.disable()
            db.close()
    return 0


def existing_server_status(host: str, port: int) -> dict | None:
    """같은 주소에 arca-archive 서버가 이미 있으면 그 상태를, 없으면 None을 돌려줍니다."""
    import json as _json
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=2) as response:
            data = _json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, dict) and "scheduler_enabled" in data else {}
    except Exception:
        return None


def cmd_run(args, settings: Settings) -> int:
    from .pipeline.runner import run_channel

    db = open_db(settings)
    channel = db.get_channel_by_slug(args.channel)
    if channel is None:
        channel = db.create_channel(args.channel)
        print(f"채널 {args.channel} 을(를) 새로 등록했습니다.")
    if args.pages:
        db.update_channel(channel["id"], initial_pages=args.pages, max_pages_per_run=args.pages)
    stop_event = threading.Event()
    try:
        run = run_channel(settings, db, channel["id"], trigger="cli", stop_event=stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        print("중지 요청됨")
        return 130
    print(json.dumps({k: run[k] for k in ("id", "status", "code", "message", "stats")}, ensure_ascii=False, indent=2))
    return 0 if run["status"] == "success" else (3 if run["status"] == "partial" else 1)


def cmd_login(args, settings: Settings) -> int:
    from .fetch.browser_fetcher import open_login_window

    print("브라우저 창이 열립니다. 아카라이브에 직접 로그인한 뒤 창을 닫으세요.")
    try:
        open_login_window(settings, args.url)
    except AppError as exc:
        print(str(exc))
        if exc.code in ("LOGIN_WINDOW_OPEN", "BROWSER_PROFILE_IN_USE"):
            print("이미 열린 로그인 창에서 로그인하거나, 진행 중인 수집이 끝난 뒤 다시 실행하세요.")
        return 1
    print("창이 닫혔습니다. GUI의 '세션 확인'으로 로그인 상태를 확인하세요.")
    return 0


def cmd_check_session(args, settings: Settings) -> int:
    from .fetch.browser_fetcher import check_session

    print(json.dumps(check_session(settings), ensure_ascii=False, indent=2))
    return 0


def cmd_add_channel(args, settings: Settings) -> int:
    from .sites import get_site

    db = open_db(settings)
    adapter = get_site(args.site)
    try:
        info = adapter.normalize_channel_input(args.slug)
    except ValueError as exc:
        print(f"등록 실패: {exc}")
        return 1
    if db.get_channel_by_slug(info["slug"]):
        print("이미 등록된 채널입니다.")
        return 0
    db.create_channel(info["slug"], site=adapter.key, site_channel_id=info["site_channel_id"], name=info.get("name"),
                      category=info.get("category"), fetch_mode=args.mode, interval_minutes=args.interval)
    print(f"채널 등록 완료: [{adapter.label}] {info['slug']} ({info.get('name') or ''})")
    return 0


def cmd_channels(args, settings: Settings) -> int:
    db = open_db(settings)
    for c in db.list_channels():
        counts = db.count_articles_by_state(c["id"])
        print(f"{c['id']:>3} {c['slug']:<20} enabled={c['enabled']} mode={c['fetch_mode']} browser={c['requires_browser']} "
              f"last_run={c['last_run_at']} next={c['next_run_at']} articles={counts}")
    return 0


def cmd_backup(args, settings: Settings) -> int:
    from datetime import datetime

    db = open_db(settings)
    folder = Path(args.dest) if args.dest else settings.data_path / "backups"
    path = db.backup_to(folder / f"arca-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3")
    print(f"백업 완료: {path} ({path.stat().st_size/1e6:.1f} MB)")
    print("미디어 파일(data/media)과 원본 HTML(data/raw)은 폴더를 그대로 복사해 보관하세요.")
    return 0


def cmd_export(args, settings: Settings) -> int:
    from .export import export_articles_jsonl

    db = open_db(settings)
    channel = db.get_channel_by_slug(args.channel) if args.channel else None
    if args.channel and channel is None:
        print("채널이 없습니다."); return 1
    out = Path(args.out) if args.out else settings.data_path / "exports" / f"articles-{args.channel or 'all'}.jsonl"
    count = export_articles_jsonl(db, out, channel["id"] if channel else None)
    print(f"내보내기 완료: {out} ({count}개 글)")
    return 0


def cmd_stats(args, settings: Settings) -> int:
    db = open_db(settings)
    print(json.dumps({"articles": db.count_articles_by_state(), "media": db.count_media_by_state(),
                      "runs": db.list_runs(5)}, ensure_ascii=False, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arca-archive", description="아카라이브 채널 수집 시스템")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="GUI 서버 + 스케줄러 실행")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("run", help="채널 1회 수집")
    p.add_argument("--channel", required=True)
    p.add_argument("--pages", type=int, help="이번 실행의 목록 페이지 수 제한")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("login", help="로그인용 브라우저 창 열기")
    p.add_argument("--url", default="https://arca.live/")
    p.set_defaults(func=cmd_login)
    p = sub.add_parser("check-session", help="브라우저 세션 로그인 상태 확인")
    p.set_defaults(func=cmd_check_session)
    p = sub.add_parser("add-channel", help="채널 등록")
    p.add_argument("slug")
    p.add_argument("--mode", default="auto", choices=["auto", "http", "browser"])
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--site", default="arca", choices=["arca", "naver_cafe"])
    p.set_defaults(func=cmd_add_channel)
    p = sub.add_parser("channels", help="채널 목록")
    p.set_defaults(func=cmd_channels)
    p = sub.add_parser("stats", help="누적 통계")
    p.set_defaults(func=cmd_stats)
    p = sub.add_parser("backup", help="DB 온라인 백업(data/backups)")
    p.add_argument("--dest")
    p.set_defaults(func=cmd_backup)
    p = sub.add_parser("export", help="글·미디어 경로·댓글을 JSONL로 내보내기")
    p.add_argument("--channel")
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        setup_logging(settings, console=args.command != "login")
        return args.func(args, settings)
    except AppError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
