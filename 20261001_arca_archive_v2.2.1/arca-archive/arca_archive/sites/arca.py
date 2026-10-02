"""아카라이브 어댑터: 기존 parsers/ 모듈을 어댑터 인터페이스로 감쌉니다."""
from __future__ import annotations

import re
from urllib.parse import quote

from ..fetch.base import FetchResult
from ..parsers import article_page as article_parser
from ..parsers.list_page import parse_list_page
from ..parsers.page_state import PageState, classify
from .base import ArticleData, ArticleParseError, ListParse, ListRowData, Request, SitePolicy


class ArcaSite:
    key = "arca"
    label = "아카라이브"
    login_url = "https://arca.live/u/login?goto=%2F"
    input_hint = "채널 slug 또는 URL (예: ailove, https://arca.live/b/ailove)"
    policy = SitePolicy()

    def normalize_channel_input(self, text: str) -> dict:
        text = (text or "").strip()
        match = re.search(r"/b/([^/?#]+)", text)
        slug = (match.group(1) if match else text.strip("/").split("?")[0].split("#")[0]).strip()
        if not slug or not re.fullmatch(r"[A-Za-z0-9_\-]+", slug):
            raise ValueError("채널 slug가 올바르지 않습니다.")
        category = None
        cat = re.search(r"[?&]category=([^&#]+)", text)
        if cat:
            category = cat.group(1)
        return {"slug": slug, "name": None, "site_channel_id": slug, "category": category}

    def channel_url(self, channel: dict) -> str:
        return f"https://arca.live/b/{channel['slug']}"

    def internal_article_id(self, channel: dict, remote_id: str) -> int:
        return int(remote_id)  # 아카라이브 글 번호는 사이트 전역 고유값

    def internal_comment_id(self, channel: dict, remote_id: str) -> int:
        return int(remote_id)

    def list_request(self, channel: dict, page: int) -> Request:
        url = f"https://arca.live/b/{channel['slug']}?p={page}"
        if channel.get("category"):
            url += f"&category={quote(channel['category'], safe='%')}"
        return Request(url=url, kind="html")

    def parse_list(self, result: FetchResult, channel: dict) -> ListParse:
        page = parse_list_page(result.html, channel["slug"])
        rows = [ListRowData(remote_id=str(r.id), url=r.url, title=r.title, category=r.category, badges=r.badges,
                            author=r.author, created_at=r.created_at, view_count=r.view_count, like_count=r.like_count,
                            comment_count=r.comment_count, channel_local_no=r.channel_local_no, is_notice=r.is_notice)
                for r in page.rows]
        return ListParse(rows=rows, has_next=page.has_next, hidden_for_anonymous=page.hidden_for_anonymous,
                         logged_in=page.logged_in, channel_name=page.channel_name)

    def article_request(self, channel: dict, article: dict) -> Request:
        return Request(url=article["url"], kind="html")

    def classify(self, result: FetchResult, kind: str) -> PageState:
        return classify(result.status, result.final_url, result.html, result.headers)

    def parse_article(self, result: FetchResult, article: dict, channel: dict, media_hosts: list[str],
                      parse_comments: bool) -> ArticleData:
        try:
            parsed = article_parser.parse_article_page(result.html, expected_id=int(article["remote_id"]),
                                                       allowed_hosts=media_hosts, parse_comments=parse_comments)
        except article_parser.ArticleParseError as exc:
            raise ArticleParseError(exc.code, exc.message) from None
        comments = [{"remote_id": str(c.id), "parent_remote_id": str(c.parent_id) if c.parent_id else None,
                     "author": c.author, "created_at": c.created_at, "body_html": c.body_html, "body_text": c.body_text,
                     "is_deleted": c.is_deleted} for c in parsed.comments]
        return ArticleData(title=parsed.title, body_html=parsed.body_html, body_text=parsed.body_text, body_hash=parsed.body_hash,
                           category=parsed.category, badges=parsed.badges, author=parsed.author, author_type=parsed.author_type,
                           created_at=parsed.created_at, edited_at=parsed.edited_at, view_count=parsed.view_count,
                           like_count=parsed.like_count, dislike_count=parsed.dislike_count, comment_count=parsed.comment_count,
                           media=[m.as_dict() for m in parsed.media], comments=comments, warnings=parsed.warnings)

    def media_hosts(self, configured: list[str]) -> list[str]:
        return list(configured)

    def session_cookie_names(self) -> tuple[str, ...]:
        return ("arca.nick",)
