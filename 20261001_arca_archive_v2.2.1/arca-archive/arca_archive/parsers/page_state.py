"""응답 상태 분류. 세션 수준 문제(중단)와 글 단위 문제(건너뜀)를 구분합니다."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PageState:
    kind: str          # ok | challenge | legal_block | login_required | not_found | deleted | rate_limited | server_error | unknown
    code: str
    message: str
    stop: bool         # True면 실행 전체를 중단해야 하는 세션 수준 문제
    retryable: bool    # 글 단위 재시도 대상인지


OK = PageState("ok", "OK", "정상", False, False)

_DELETED_PATTERNS = (
    "삭제된 게시물", "삭제되었습니다", "존재하지 않는 게시물", "게시물이 존재하지 않", "삭제된 글",
)
_LOGIN_PATTERNS = ("로그인이 필요", "로그인 후 이용", "로그인 하신 후 열람", "성인 인증", "본인 인증")


def classify(status: int, final_url: str, html: str, headers: dict[str, str] | None = None) -> PageState:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    title = _title(html)
    if headers.get("cf-mitigated") == "challenge" or "Just a moment" in title or 'id="challenge-error-text"' in html:
        return PageState("challenge", "CF_CHALLENGE", "Cloudflare 확인 화면이 나타났습니다. 브라우저 모드 또는 대기 후 재시도가 필요합니다.", True, True)
    path = urlsplit(final_url or "").path if final_url else ""
    if path.startswith("/u/login") or path.startswith("/login"):
        return PageState("login_required", "LOGIN_REQUIRED", "로그인 페이지로 이동했습니다. 브라우저 세션에 로그인하세요.", True, True)
    if status == 451:
        return PageState("legal_block", "BLOCKED_451", "HTTP 451: 이 채널/게시글은 로그인·성인 인증된 세션에서만 열 수 있습니다.", True, True)
    if status == 429:
        return PageState("rate_limited", "RATE_LIMITED", "요청 제한(429)을 받았습니다. 대기 후 재시도합니다.", True, True)
    if status == 404:
        if any(p in html for p in _DELETED_PATTERNS):
            return PageState("deleted", "ARTICLE_DELETED", "삭제된 게시글입니다.", False, False)
        return PageState("not_found", "NOT_FOUND", "존재하지 않는 페이지입니다.", False, False)
    if status in (401, 403):
        if any(p in html for p in _LOGIN_PATTERNS):
            return PageState("login_required", "LOGIN_REQUIRED", "로그인 또는 인증이 필요한 페이지입니다.", True, True)
        return PageState("server_error", "FORBIDDEN", f"접근이 거부되었습니다(HTTP {status}).", True, True)
    if status >= 500:
        return PageState("server_error", "SERVER_ERROR", f"서버 오류(HTTP {status}).", False, True)
    if status != 200:
        return PageState("unknown", f"HTTP_{status}", f"예상하지 못한 응답(HTTP {status}).", False, True)
    if any(p in html for p in _DELETED_PATTERNS) and "article-content" not in html:
        return PageState("deleted", "ARTICLE_DELETED", "삭제된 게시글입니다.", False, False)
    return OK


def _title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html[:5000], re.S | re.I)
    return match.group(1).strip() if match else ""


def is_logged_in(html: str) -> bool | None:
    """페이지 HTML로 로그인 여부를 추정합니다. 판단 근거가 없으면 None."""
    head = html[:200000]
    if re.search(r'href="/u/logout', head):
        return True
    if re.search(r'href="/u/login', head):
        return False
    return None


def has_sensitive_media_flag(html: str) -> bool:
    return bool(re.search(r'<meta name="has_sensitive_media" content="1"', html[:20000]))
