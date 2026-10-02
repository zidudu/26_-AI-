"""네이버 카페 어댑터.

확인한 구조(2026-09-19):
- 카페 정보: GET https://apis.naver.com/cafe-web/cafe2/CafeGateInfo.json?cluburl={카페URL이름}
    → message.result.cafeInfoView.{cafeId, cafeName, cafeUrl, openType}
- 글 목록:  GET https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafeId}/menus/{menuId}/articles
              ?page=N&pageSize=50&sortBy=TIME&viewType=L   (헤더 x-cafe-product: pc)
    → result.articleList[].item.{articleId, subject, writerInfo.nickName, writeDateTimestamp(ms), readCount,
      commentCount, likeCount, menuId, menuName, readLevel, restrictMenu, blindArticle, hasImage}
    공개 카페는 비로그인으로도 목록이 옵니다. 최신순(ID 내림차순).
- 글 본문:  GET https://article.cafe.naver.com/gw/v3/cafes/{cafeId}/articles/{articleId}?query=&useCafeId=true&requestFrom=A
    비로그인은 401 {"result":{"errorCode":"0004","reason":"로그인하지 않았습니다."}}. 로그인 세션(브라우저 프로필 쿠키)이 있어야
    하며 회원 전용/등급 게시판은 해당 카페 가입·등급이 필요합니다. 응답 본문의 정확한 형태는 로그인 세션으로만 볼 수 있어
    여러 후보 키를 관용적으로 읽고, 원본 JSON을 그대로 보관합니다.
- 이미지 CDN: https://cafeptthumb-phinf.pstatic.net/.../파일.jpg?type=w1600  → `?type=` 을 떼면 원본. Referer 불필요.

지키는 선: 자동 로그인·캡챠 우회 없음(사용자가 일반 Chrome에서 직접 로그인), 가입하지 않았거나 등급이 안 되는
게시판은 `blocked`로 기록하고 재시도하지 않음, 요청 간격은 공통 속도 제한을 따름.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from lxml import etree
from lxml import html as lxml_html

from ..common import sha256_text
from ..fetch.base import FetchResult
from ..parsers.article_page import _body_text, extract_media
from ..parsers.page_state import OK, PageState
from .base import ArticleData, ArticleParseError, ListParse, ListRowData, Request, SitePolicy

ID_SPACE = 10 ** 12  # 내부 ID = channel_id * ID_SPACE + 카페 내 글 번호 (아카라이브 전역 ID와 겹치지 않음)
PAGE_SIZE = 50
API_HEADERS = {"x-cafe-product": "pc", "Accept": "application/json, text/plain, */*"}
MEDIA_HOSTS = ("pstatic.net",)
_LOGIN_HINTS = ("로그인", "login")
_DELETED_HINTS = ("삭제", "존재하지 않", "없는 게시글", "찾을 수 없")
_RESTRICTED_HINTS = ("등급", "가입", "권한", "멤버", "회원", "열람", "접근")


def _ms_to_iso(value) -> str | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    if ms > 10 ** 11:  # 밀리초
        ms //= 1000
    return datetime.fromtimestamp(ms, tz=timezone.utc).replace(microsecond=0).isoformat()


def _pick(d: dict, *keys, default=None):
    for key in keys:
        if isinstance(d, dict) and key in d and d[key] is not None:
            return d[key]
    return default


def strip_image_params(url: str) -> str:
    """CDN 크기 파라미터(`?type=w1600`)를 떼면 원본입니다."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class NaverCafeSite:
    key = "naver_cafe"
    label = "네이버 카페"
    login_url = "https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fcafe.naver.com%2F"
    input_hint = "카페 URL 이름 또는 주소 (예: iroid, https://cafe.naver.com/iroid). 특정 게시판만 원하면 메뉴 주소(.../menus/123)"
    # 요청은 모두 실제 브라우저에서 나가게 하고(페이지 열기·페이지 내 fetch·탭 탐색), 사람이 읽는 속도로만 움직입니다.
    policy = SitePolicy(prefer_browser=True, natural_page_loads=True, in_page_media=True,
                        min_page_delay=3.0, page_delay_jitter=2.0, max_articles_per_run=60)

    # ------------------------------------------------------------------ 채널
    def normalize_channel_input(self, text: str, resolver=None) -> dict:
        text = (text or "").strip()
        menu = None
        m_menu = re.search(r"/menus/(\d+)", text) or re.search(r"[?&]search\.menuid=(\d+)", text) or re.search(r"[?&]menuId=(\d+)", text)
        if m_menu:
            menu = m_menu.group(1)
        cafe_id = None
        club = None
        m_id = re.search(r"/(?:f-e|ca-fe)/cafes/(\d+)", text)
        if m_id:
            cafe_id = m_id.group(1)
        elif re.fullmatch(r"\d+", text):
            cafe_id = text
        else:
            m_club = re.search(r"cafe\.naver\.com/([A-Za-z0-9_\-]+)", text)
            club = (m_club.group(1) if m_club else text.strip("/").split("?")[0].split("#")[0]).strip()
            if club in ("f-e", "ca-fe") or not re.fullmatch(r"[A-Za-z0-9_\-]+", club or ""):
                raise ValueError("카페 URL 이름이 올바르지 않습니다.")
        info = (resolver or self.resolve_cafe)(club=club, cafe_id=cafe_id)
        return {"slug": info["cafe_url"], "name": info["cafe_name"], "site_channel_id": str(info["cafe_id"]), "category": menu}

    def resolve_cafe(self, club: str | None = None, cafe_id: str | None = None) -> dict:
        import httpx

        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://cafe.naver.com/", **API_HEADERS}
        with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
            if club:
                data = client.get("https://apis.naver.com/cafe-web/cafe2/CafeGateInfo.json", params={"cluburl": club}).json()
                message = data.get("message") or {}
                view = ((message.get("result") or {}).get("cafeInfoView")) or {}
                if not view.get("cafeId"):
                    raise ValueError((message.get("error") or {}).get("msg") or "카페를 찾지 못했습니다.")
                return {"cafe_id": int(view["cafeId"]), "cafe_name": view.get("cafeName") or club, "cafe_url": view.get("cafeUrl") or club}
            # 숫자 ID만 있으면 목록 API로 존재 여부를 확인하고 이름은 목록 첫 항목에서 가져옵니다.
            r = client.get(f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe_id}/menus/0/articles",
                           params={"page": 1, "pageSize": 1, "sortBy": "TIME", "viewType": "L"})
            if r.status_code != 200:
                raise ValueError("카페 ID로 목록을 읽지 못했습니다.")
            return {"cafe_id": int(cafe_id), "cafe_name": f"cafe {cafe_id}", "cafe_url": f"cafe{cafe_id}"}

    def channel_url(self, channel: dict) -> str:
        return f"https://cafe.naver.com/f-e/cafes/{channel['site_channel_id']}/menus/{channel.get('category') or 0}"

    def internal_article_id(self, channel: dict, remote_id: str) -> int:
        return int(channel["id"]) * ID_SPACE + int(remote_id)

    def internal_comment_id(self, channel: dict, remote_id: str) -> int:
        return int(channel["id"]) * ID_SPACE + int(remote_id)

    # ------------------------------------------------------------------ 목록
    def list_request(self, channel: dict, page: int) -> Request:
        cafe_id = channel["site_channel_id"]
        menu = channel.get("category") or "0"
        url = (f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe_id}/menus/{menu}/articles"
               f"?page={page}&pageSize={PAGE_SIZE}&sortBy=TIME&viewType=L")
        # 브라우저 모드에서는 카페 페이지 위에서 페이지 내 fetch 로 호출됩니다(SPA와 같은 요청).
        return Request(url=url, kind="json", headers={"x-cafe-product": "pc"},
                       origin=f"https://cafe.naver.com/f-e/cafes/{cafe_id}/menus/{menu}")

    def parse_list(self, result: FetchResult, channel: dict) -> ListParse:
        data = _json(result.html)
        res = data.get("result") if isinstance(data, dict) else None
        if not isinstance(res, dict):
            error = _error_of(data)
            if error and any(h in error for h in _LOGIN_HINTS):
                return ListParse(rows=[], has_next=False, hidden_for_anonymous=True, logged_in=False)
            raise ArticleParseError("LIST_FORMAT", f"목록 응답 형식이 예상과 다릅니다: {error or '알 수 없음'}")
        cafe_id = channel["site_channel_id"]
        rows: list[ListRowData] = []
        for entry in res.get("articleList") or []:
            item = entry.get("item") if isinstance(entry, dict) and "item" in entry else entry
            if not isinstance(item, dict) or item.get("articleId") is None:
                continue
            if entry.get("type") not in (None, "ARTICLE"):
                continue
            writer = item.get("writerInfo") or {}
            aid = int(item["articleId"])
            rows.append(ListRowData(
                remote_id=str(aid),
                url=f"https://cafe.naver.com/f-e/cafes/{cafe_id}/articles/{aid}",
                title=str(item.get("subject") or "").strip(),
                category=item.get("menuName"),
                badges=[b for b in [item.get("menuName")] if b],
                author=writer.get("nickName") or writer.get("nickname"),
                created_at=_ms_to_iso(item.get("writeDateTimestamp") or item.get("writeDate")),
                view_count=_int(item.get("readCount")),
                like_count=_int(item.get("likeCount")),
                comment_count=_int(item.get("commentCount")),
                channel_local_no=aid,
                is_notice=False,
            ))
        return ListParse(rows=rows, has_next=len(rows) >= PAGE_SIZE, logged_in=None)

    # ------------------------------------------------------------------ 게시글
    def article_request(self, channel: dict, article: dict) -> Request:
        cafe_id = channel["site_channel_id"]
        remote = article["remote_id"]
        api_url = (f"https://article.cafe.naver.com/gw/v3/cafes/{cafe_id}/articles/{remote}"
                   f"?query=&useCafeId=true&requestFrom=A")
        # 기본: 실제 글 페이지를 열고 SPA가 호출하는 본문 API(gw/v3 또는 v4) 응답을 가로챕니다.
        return Request(url=f"https://cafe.naver.com/f-e/cafes/{cafe_id}/articles/{remote}", kind="page_capture",
                       capture_pattern=rf"article\.cafe\.naver\.com/gw/v\d+/cafes/{cafe_id}/articles/{remote}(\?|$)",
                       fallback=Request(url=api_url, kind="json", headers={**API_HEADERS, "Referer": "https://cafe.naver.com/"}))

    def classify(self, result: FetchResult, kind: str) -> PageState:
        status = result.status
        if kind == "page_capture" and result.headers.get("x-capture") == "timeout":
            return self._classify_uncaptured_page(result)
        data = _json(result.html) if kind in ("json", "page_capture") else None
        error = _error_of(data) if data is not None else None
        if status == 401 or (error and any(h in error for h in _LOGIN_HINTS)):
            return PageState("login_required", "LOGIN_REQUIRED", "네이버 로그인 세션이 필요합니다. 브라우저 세션에서 네이버에 로그인하세요.", True, True)
        if status == 429:
            return PageState("rate_limited", "RATE_LIMITED", "요청 제한(429)을 받았습니다. 대기 후 재시도합니다.", True, True)
        if status == 404 or (error and any(h in error for h in _DELETED_HINTS)):
            return PageState("deleted", "ARTICLE_DELETED", error or "삭제되었거나 없는 게시글입니다.", False, False)
        if status == 403 or (error and any(h in error for h in _RESTRICTED_HINTS)):
            return PageState("restricted", "ACCESS_RESTRICTED", error or "가입 또는 등급이 필요한 게시판입니다.", False, False)
        if status >= 500:
            return PageState("server_error", "SERVER_ERROR", f"서버 오류(HTTP {status}).", False, True)
        if status != 200:
            return PageState("unknown", f"HTTP_{status}", f"예상하지 못한 응답(HTTP {status}).", False, True)
        if error:
            return PageState("unknown", "API_ERROR", error, False, True)
        return OK

    @staticmethod
    def _classify_uncaptured_page(result: FetchResult) -> PageState:
        """글 페이지를 열었는데 SPA가 본문 API를 호출하지 않은 경우를 페이지 내용으로 판단합니다."""
        text = re.sub(r"<[^>]+>", " ", result.html or "")
        if result.headers.get("x-has-session") == "0" or "nidlogin" in (result.final_url or ""):
            return PageState("login_required", "LOGIN_REQUIRED", "네이버 로그인 세션이 없어 글을 열 수 없습니다. 브라우저 세션에서 네이버에 로그인하세요.", True, True)
        if any(h in text for h in ("자동입력 방지", "보안 확인", "captcha", "CAPTCHA")):
            return PageState("challenge", "CAPTCHA_PAGE", "네이버가 자동입력 방지 확인을 요구했습니다. 실행을 중단합니다. 잠시 후 브라우저에서 직접 확인하세요.", True, True)
        if any(h in text for h in _DELETED_HINTS):
            return PageState("deleted", "ARTICLE_DELETED", "삭제되었거나 없는 게시글입니다.", False, False)
        if any(h in text for h in ("카페 가입", "가입 후", "등급", "권한이 없", "멤버만")):
            return PageState("restricted", "ACCESS_RESTRICTED", "가입 또는 등급이 필요한 게시판입니다.", False, False)
        return PageState("unknown", "PAGE_NO_API", "글 페이지에서 본문 API 응답을 확인하지 못했습니다.", False, True)

    def parse_article(self, result: FetchResult, article: dict, channel: dict, media_hosts: list[str],
                      parse_comments: bool) -> ArticleData:
        data = _json(result.html)
        res = data.get("result") if isinstance(data, dict) else None
        if not isinstance(res, dict):
            raise ArticleParseError("NO_CONTENT", "게시글 응답에 result 가 없습니다.")
        art = _pick(res, "article", default=res)
        if not isinstance(art, dict):
            raise ArticleParseError("NO_CONTENT", "게시글 응답에 article 이 없습니다.")
        warnings: list[str] = []
        content_html = _pick(art, "contentHtml", "content", "body", default=None)
        if not isinstance(content_html, str) or not content_html.strip():
            raise ArticleParseError("NO_CONTENT", f"본문 HTML 키를 찾지 못했습니다(keys={sorted(art.keys())[:15]}).")
        remote = str(_pick(art, "id", "articleId", default=article["remote_id"]))
        if remote != str(article["remote_id"]):
            raise ArticleParseError("ID_MISMATCH", f"요청한 글({article['remote_id']})과 응답의 글({remote})이 다릅니다.")
        title = str(_pick(art, "subject", "title", default="") or "").strip()
        if not title:
            warnings.append("TITLE_NOT_FOUND")
        writer = _pick(art, "writer", "writerInfo", default={}) or {}
        author = _pick(writer, "nick", "nickName", "nickname", default=None) if isinstance(writer, dict) else None
        author_type = _pick(writer, "memberLevelName", default=None) if isinstance(writer, dict) else None
        menu = _pick(art, "menu", default={}) or {}
        category = _pick(menu, "name", "menuName", default=None) if isinstance(menu, dict) else _pick(art, "menuName", default=None)
        created = _ms_to_iso(_pick(art, "writeDate", "writeDateTimestamp", default=None))
        if created is None:
            warnings.append("CREATED_AT_NOT_FOUND")
        edited = _ms_to_iso(_pick(art, "modifyDate", "updateDate", default=None))
        node = lxml_html.fromstring(f"<div>{content_html}</div>")
        for bad in node.xpath('.//script|.//style'):
            bad.drop_tree()
        for comment in node.xpath('.//comment()'):
            comment.drop_tag()  # 스마트에디터 주석(<!-- SE-TEXT { -->)은 표시 내용이 아님
        body_text = _body_text(node)
        media = [m.as_dict() for m in extract_media(node, media_hosts)]
        for item in media:
            for key in ("served_url", "original_url"):
                if item.get(key) and "pstatic.net" in item[key]:
                    item["original_url"] = strip_image_params(item.get("original_url") or item["served_url"])
            item["url_expires_at"] = None
        body_html = "".join(etree.tostring(c, encoding="unicode", method="html") if isinstance(c.tag, str) else (c.text or "") for c in node)
        if node.text:
            body_html = node.text + body_html
        body_hash = sha256_text(title + "\n" + body_text + "\n" + "|".join(m["source_key"] for m in media))
        comments: list[dict] = []
        if parse_comments:
            comments = self._parse_comments(res)
        return ArticleData(
            title=title, body_html=body_html.strip(), body_text=body_text, body_hash=body_hash, category=category,
            badges=[b for b in [category] if b], author=author, author_type=author_type, created_at=created, edited_at=edited,
            view_count=_int(_pick(art, "readCount", "viewCount", default=None)),
            like_count=_int(_pick(art, "likeCount", "likeItCount", default=None)), dislike_count=None,
            comment_count=_int(_pick(art, "commentCount", default=None)), media=media, comments=comments, warnings=warnings,
        )

    @staticmethod
    def _parse_comments(res: dict) -> list[dict]:
        block = _pick(res, "comments", default=None)
        items = None
        if isinstance(block, dict):
            items = _pick(block, "items", "list", "commentList", default=None)
        elif isinstance(block, list):
            items = block
        out: list[dict] = []
        for c in items or []:
            if not isinstance(c, dict):
                continue
            cid = _pick(c, "id", "commentId", default=None)
            if cid is None:
                continue
            writer = _pick(c, "writer", "writerInfo", default={}) or {}
            content = _pick(c, "content", "contentHtml", "text", default="") or ""
            try:
                node = lxml_html.fromstring(f"<div>{content}</div>")
                text = _body_text(node)
            except Exception:
                text = re.sub(r"<[^>]+>", " ", str(content)).strip()
            parent = _pick(c, "refId", "parentId", "refCommentId", "parentCommentId", default=None)
            deleted = bool(_pick(c, "isDeleted", "deleted", default=False))
            out.append({"remote_id": str(cid), "parent_remote_id": str(parent) if parent not in (None, 0, "0") and str(parent) != str(cid) else None,
                        "author": _pick(writer, "nick", "nickName", "nickname", default=None) if isinstance(writer, dict) else None,
                        "created_at": _ms_to_iso(_pick(c, "updateDate", "writeDate", "regDate", default=None)),
                        "body_html": str(content), "body_text": text, "is_deleted": deleted})
        return out

    def media_hosts(self, configured: list[str]) -> list[str]:
        return list(dict.fromkeys([*configured, *MEDIA_HOSTS]))

    def session_cookie_names(self) -> tuple[str, ...]:
        return ("NID_AUT", "NID_SES")


def _json(text: str):
    try:
        return json.loads(text) if text and text.strip() else None
    except ValueError:
        return None


def _error_of(data) -> str | None:
    if not isinstance(data, dict):
        return None
    res = data.get("result")
    if isinstance(res, dict) and res.get("errorCode"):
        return str(res.get("reason") or res.get("message") or res.get("errorCode"))
    message = data.get("message")
    if isinstance(message, dict):
        err = message.get("error") or {}
        if isinstance(err, dict) and err.get("code"):
            return str(err.get("msg") or err.get("code"))
    if data.get("errorCode") or data.get("error"):
        return str(data.get("message") or data.get("error") or data.get("errorCode"))
    return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
