"""V1에서 검증한 게시글 본문 수집 및 JSON 저장 모듈입니다.

V5 sync_runner.py가 이력을 확인한 수집 대상을 이 모듈로 전달합니다.
웹 수집은 collect_article(), 저장은 save_article()로 분리했습니다.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from .page_guard import assert_session, assert_article_access

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
VERSION = "7.5.2"
FRAME = "iframe#cafe_main"
TITLE = "h3.title_text"
DATE = ".article_info .date"
BODY = ".se-main-container"
BODY_SELECTORS = (BODY, ".article_viewer .ContentRenderer")


class CollectorError(Exception):
    """이 오류의 설명에는 쿠키, 비밀번호, 원본 예외 문자열을 넣지 않습니다."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class Target:
    cafe_id: str
    article_id: str

    @property
    def url(self) -> str:
        # 검색 링크에 붙은 일회성 쿼리는 결과 파일에 보관하지 않습니다.
        return f"https://cafe.naver.com/f-e/cafes/{self.cafe_id}/articles/{self.article_id}"


def parse_target(url: str, cafe_id: str, cafe_slug: str) -> Target:
    """지원되는 네이버 게시글 주소만 허용하고 게시글 식별자를 추출합니다."""
    try:
        parts = urlsplit(url.strip())
        valid = (parts.scheme == "https" and parts.hostname == "cafe.naver.com"
                 and parts.port in (None, 443) and not parts.username
                 and not parts.password)
    except ValueError:
        valid = False
    if not valid:
        raise CollectorError("INVALID_URL", "https://cafe.naver.com의 게시글 주소를 입력하세요.")
    match = re.fullmatch(r"/(?:f-e/|ca-fe/)?cafes/(\d+)/articles/(\d+)/?", parts.path)
    if match:
        found_cafe, article_id = match.groups()
    elif parts.path.lower() == "/articleread.nhn":
        query = parse_qs(parts.query)
        found_cafe = query.get("clubid", [""])[0]
        article_id = query.get("articleid", [""])[0]
    else:
        short = re.fullmatch(r"/" + re.escape(cafe_slug) + r"/(\d+)/?", parts.path)
        if not short:
            raise CollectorError("INVALID_URL", "카페 홈이 아닌 개별 게시글 주소가 필요합니다.")
        found_cafe, article_id = cafe_id, short.group(1)
    if (not re.fullmatch(r"[1-9]\d*", found_cafe)
            or not re.fullmatch(r"[1-9]\d*", article_id)):
        raise CollectorError("INVALID_URL", "카페 ID와 게시글 ID는 양의 정수여야 합니다.")
    if found_cafe != cafe_id:
        raise CollectorError("CAFE_MISMATCH", "주소의 카페 ID와 config_v5.json의 cafe_id가 다릅니다.")
    return Target(found_cafe, article_id)


def read_config(path: Path, override_url: str | None = None) -> tuple[dict, Target]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise CollectorError("CONFIG_ERROR", "config_v5.json을 읽을 수 없습니다. 파일과 JSON 문법을 확인하세요.") from None
    if not isinstance(cfg, dict):
        raise CollectorError("CONFIG_ERROR", "설정은 JSON 객체여야 합니다.")
    for key in ("cafe_id", "cafe_slug", "target_url", "profile_dir", "output_dir"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise CollectorError("CONFIG_ERROR", f"설정의 {key}에는 비어 있지 않은 문자열이 필요합니다.")
    timeout = cfg.get("timeout_seconds", 40)
    if type(timeout) is not int or not 5 <= timeout <= 120:
        raise CollectorError("CONFIG_ERROR", "timeout_seconds는 5~120 사이 정수여야 합니다.")
    cfg["timeout_seconds"] = timeout
    if cfg.get("browser", "chromium") not in ("chromium", "chrome", "msedge"):
        raise CollectorError("CONFIG_ERROR", "browser는 chromium, chrome, msedge 중 하나여야 합니다.")
    cfg["browser"] = cfg.get("browser", "chromium")
    for key in ("profile_dir", "output_dir"):
        cfg[key] = (path.parent / cfg[key]).resolve()
    if cfg["profile_dir"] == cfg["output_dir"] or cfg["profile_dir"] in cfg["output_dir"].parents or cfg["output_dir"] in cfg["profile_dir"].parents:
        raise CollectorError("CONFIG_ERROR", "프로필 폴더와 결과 폴더는 서로 분리해야 합니다.")
    target = parse_target(override_url or cfg["target_url"], cfg["cafe_id"], cfg["cafe_slug"])
    return cfg, target


def normalize_date(raw: str) -> str:
    match = re.fullmatch(r"\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\.\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*", raw)
    if not match:
        raise CollectorError("INVALID_DATE", "작성일 형식이 예상과 다릅니다. 날짜 요소를 확인해야 합니다.")
    try:
        values = [int(x or 0) for x in match.groups()]
        return datetime(*values, tzinfo=KST).isoformat()
    except ValueError:
        raise CollectorError("INVALID_DATE", "유효하지 않은 작성일입니다.") from None


def build_article(raw: dict, target: Target) -> dict:
    """DOM 읽기 결과를 검증합니다. 원문은 그대로, 정리된 본문은 별도로 저장합니다."""
    title, date_raw, body_raw = (raw.get(k) for k in ("title", "date", "body"))
    if not isinstance(title, str) or not title.strip():
        raise CollectorError("EMPTY_TITLE", "게시글 제목을 읽지 못했습니다.")
    if not isinstance(date_raw, str):
        raise CollectorError("INVALID_DATE", "작성일을 읽지 못했습니다.")
    if not isinstance(body_raw, str):
        raise CollectorError("EMPTY_BODY", "본문 영역을 읽지 못했습니다.")
    body = body_raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    media = raw.get('media') or {}
    image_count = media.get('image_count', 0)
    if not body and not (type(image_count) is int and image_count > 0):
        raise CollectorError("EMPTY_BODY", "본문 텍스트가 비어 있습니다. 이미지 전용 글 또는 추출 실패를 확인하세요.")
    # 로그인·오류 화면의 문장만 추출됐을 때 성공으로 취급하지 않습니다.
    if body in ("로딩중입니다.", "로그인이 필요합니다.", "삭제된 게시글입니다."):
        raise CollectorError("INVALID_BODY", "게시글 본문 대신 상태 안내가 읽혔습니다.")
    article = {
        "schema_version": "5.0", "collector_version": VERSION,
        "cafe_id": target.cafe_id, "article_id": target.article_id,
        "title": title.strip(), "written_at_raw": date_raw.strip(),
        "written_at": normalize_date(date_raw),
        "body_raw": body_raw, "body": body, "body_char_count": len(body),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "url": target.url, "collected_at": datetime.now(KST).isoformat(),
        "collection_method": "playwright_rendered_dom", "status": "collected",
        "content_kind": "image_only" if not body else ("text_with_images" if image_count else "text"),
        "media": media, "metadata": raw.get('metadata') or {},
    }
    if raw.get("body_selector") in BODY_SELECTORS:
        article["body_selector"] = raw["body_selector"]
    return article


def save_article(output_dir: Path, article: dict) -> Path:
    """일부만 쓴 파일이 결과로 남지 않게 임시 파일 작성 후 이름을 바꿉니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{article['cafe_id']}_{article['article_id']}_{stamp}_{uuid4().hex[:8]}.json"
    destination = output_dir / filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_dir,
                                         prefix=".partial_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(article, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_run(output_dir: Path, status: str, code: str, target: Target, result: Path | None = None) -> None:
    """실행 상태만 기록합니다. 로그인 정보·브라우저 예외 전문·원문은 로그에 넣지 않습니다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.now(KST).isoformat(), "status": status, "code": code,
             "cafe_id": target.cafe_id, "article_id": target.article_id}
    if result:
        entry["result_file"] = result.name
    with (output_dir / "runs.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def has_login_redirect(page) -> bool:
    # 인증 입력값을 읽지 않고 프레임의 이동 대상 호스트만 확인합니다.
    return any(urlsplit(frame.url).hostname == "nid.naver.com" for frame in page.frames)


def assert_target_page(page, target: Target, cafe_slug: str) -> None:
    if has_login_redirect(page):
        raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login_v5.bat 실행 후 다시 수집하세요.")
    try:
        actual = parse_target(page.url, target.cafe_id, cafe_slug)
    except CollectorError:
        raise CollectorError("WRONG_PAGE", "대상 게시글이 아닌 화면으로 이동했습니다. 로그인·주소·열람 권한을 확인하세요.") from None
    if actual != target:
        raise CollectorError("WRONG_ARTICLE", "다른 게시글로 이동하여 수집을 중단했습니다.")


def body_extraction_script():
    """준비 검사에서 선택한 본문 요소를 그대로 읽습니다. 전체 페이지로 범위를 넓히지 않습니다."""
    extractor = (ROOT / 'extract_article.js').read_text(encoding='utf-8')
    return '''selection => {
        const b = document.querySelectorAll(selection.selector)[selection.index];
        if (!b || b.getClientRects().length === 0) return null;
        const result = (''' + extractor + ''')(b);
        if (!/[^\\s\\u200b]/.test(result.body || '') && !result.media.image_count) return null;
        result.body_selector = selection.selector;
        result.body_index = selection.index;
        return result;
    }'''


def wait_for_article(page, cfg):
    """본문 준비와 제한 안내를 0.25초 간격으로 함께 확인합니다. 전체 대기 예산은 1회입니다."""
    deadline = time.monotonic() + cfg['timeout_seconds']
    diagnostics = {'stage': 'article', 'frame_detected': False,
                   'title_visible': False, 'date_visible': False,
                   'body_visible': False, 'body_char_count': 0}
    extractor = body_extraction_script()
    while True:
        states = assert_article_access(page)
        frame = page.frame(name='cafe_main')
        diagnostics['frame_detected'] = frame is not None
        state = next((s for f, s in states if f == frame), {})
        for key in ('title_visible', 'date_visible', 'body_visible', 'body_char_count'):
            diagnostics[key] = state.get(key, False if key != 'body_char_count' else 0)
        diagnostics['body_selector'] = state.get('body_selector')
        diagnostics['body_index'] = state.get('body_index')
        if frame is not None and state.get('ready') is True:
            selector = state.get('body_selector', BODY)
            index = state.get('body_index', 0)
            if selector not in BODY_SELECTORS or type(index) is not int or index < 0:
                raise CollectorError('BODY_SELECTOR_MISMATCH', '본문 검사 파일과 수집 코드가 맞지 않습니다. 수정본의 v5 폴더를 함께 덮어쓰세요.')
            raw = frame.evaluate(extractor, {'selector': selector, 'index': index})
            if isinstance(raw, dict) and isinstance(raw.get('body'), str) and (raw['body'].strip() or (raw.get('media') or {}).get('image_count', 0)):
                return raw
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectorError('PAGE_NOT_READY',
                '본문 요소를 제한 시간 안에 확인하지 못했습니다. 원인 미확인으로 기록합니다.', diagnostics)
        # sync Playwright의 이벤트 처리를 유지하며 다음 화면 상태를 확인합니다.
        page.wait_for_timeout(min(250, remaining * 1000))


def collect_article(page, target: Target, cfg: dict, timeout_error, navigation_url: str | None = None) -> dict:
    """지정 URL을 한 번 열고 검증된 프레임 구조에서 본문을 읽습니다."""
    timeout_ms = cfg["timeout_seconds"] * 1000
    try:
        if navigation_url and parse_target(navigation_url, target.cafe_id, cfg["cafe_slug"]) != target:
            raise CollectorError("WRONG_ARTICLE", "검색 결과의 게시글 ID가 선택 대상과 다릅니다.")
        response = page.goto(navigation_url or target.url, wait_until="domcontentloaded", timeout=timeout_ms)
        assert_article_access(page)
        if response and response.status in (403, 429):
            raise CollectorError("REQUEST_BLOCKED", f"페이지 요청 제한(HTTP {response.status})으로 중단합니다.")
        if response and response.status >= 400:
            raise CollectorError("HTTP_ERROR", f"페이지 요청에 HTTP {response.status} 오류가 반환됐습니다.")
        if has_login_redirect(page):
            raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login_v5.bat 실행 후 다시 수집하세요.")
        assert_target_page(page, target, cfg["cafe_slug"])

        raw = wait_for_article(page, cfg)

        assert_target_page(page, target, cfg["cafe_slug"])
        # 내부 프레임에도 명시적인 게시글 ID가 있으면 요청한 ID와 일치해야 합니다.
        document_url = raw.pop("document_url", "")
        if "/articles/" in document_url or "articleid=" in document_url.lower():
            inner_target = parse_target(document_url, target.cafe_id, cfg["cafe_slug"])
            if inner_target != target:
                raise CollectorError("WRONG_ARTICLE", "내부 프레임의 게시글 ID가 요청과 다릅니다.")
        article = build_article(raw, target)
        from .metadata import initialize_metadata
        initialize_metadata(page, article)
        if cfg.get('capture_output_dir') is not None:
            from .post_capture import capture_post
            article['capture'] = capture_post(page, raw, cfg['capture_output_dir'], article)
        return article
    except (timeout_error, AssertionError):
        assert_article_access(page)
        if has_login_redirect(page):
            raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login_v5.bat 실행 후 다시 수집하세요.") from None
        raise CollectorError("PAGE_NOT_READY", "페이지 이동 시간이 초과됐습니다. 원인 미확인으로 기록합니다.", {"stage": "navigation"}) from None


def login(context, target: Target, cfg: dict, timeout_error) -> None:
    """사용자가 전용 브라우저에서 직접 로그인합니다. 비밀번호는 프로그램이 읽지 않습니다."""
    page = context.new_page()
    from urllib.parse import quote
    page.goto("https://nid.naver.com/nidlogin.login?url=" + quote(target.url, safe=""),
              wait_until="domcontentloaded", timeout=cfg["timeout_seconds"] * 1000)
    print("\n열린 브라우저에서 직접 네이버 로그인을 완료하세요.")
    print("브라우저는 닫지 말고 이 콘솔로 돌아오세요. 추가 인증이 있으면 직접 완료하세요.")
    answer = input("로그인 완료 후 Enter / 취소는 q 입력 후 Enter: ").strip().lower()
    if answer == "q":
        raise CollectorError("CANCELLED", "로그인 확인을 취소했습니다.")
    # 성공 여부는 새 탭에서 실제 대상 글을 읽어 판정합니다.
    verification = context.new_page()
    article = collect_article(verification, target, cfg, timeout_error)
    print(f"[확인 완료] 대상 글 열람 가능: {article['title']}")
    print("브라우저 프로필을 보존합니다. 이제 03_sync_new.bat를 실행하세요.")
