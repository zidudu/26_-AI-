"""사이트 어댑터 인터페이스.

파이프라인(discover/collect/media/recheck), 저장소, GUI는 사이트를 모릅니다.
사이트별 차이(목록/글 요청 방법, 응답 해석, 상태 분류, 미디어 호스트, 로그인 주소)만 어댑터가 맡습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..fetch.base import FetchResult
from ..parsers.page_state import PageState


@dataclass
class ListRowData:
    remote_id: str
    url: str
    title: str
    category: str | None = None
    badges: list[str] = field(default_factory=list)
    author: str | None = None
    created_at: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    channel_local_no: int | None = None
    is_notice: bool = False


@dataclass
class ListParse:
    rows: list[ListRowData]
    has_next: bool
    hidden_for_anonymous: bool = False   # 로그인하지 않아 일반 글이 숨겨진 상태
    logged_in: bool | None = None
    channel_name: str | None = None


@dataclass
class CommentPage:
    rows: list[dict]
    total: int
    invalid_rows: int = 0


@dataclass
class ArticleData:
    title: str
    body_html: str
    body_text: str
    body_hash: str
    category: str | None = None
    badges: list[str] = field(default_factory=list)
    author: str | None = None
    author_type: str | None = None
    created_at: str | None = None
    edited_at: str | None = None
    view_count: int | None = None
    like_count: int | None = None
    dislike_count: int | None = None
    comment_count: int | None = None
    media: list[dict] = field(default_factory=list)      # MediaRef.as_dict() 형식
    comments: list[dict] = field(default_factory=list)   # remote_id, parent_remote_id, author, created_at, body_html, body_text, is_deleted
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("title", "category", "badges", "author", "author_type", "created_at", "edited_at",
                                              "view_count", "like_count", "dislike_count", "comment_count", "body_html",
                                              "body_text", "body_hash")}


@dataclass
class Request:
    url: str
    kind: str = "html"                  # html: 페이지 문서 | json: API 응답 | page_capture: 페이지를 열고 스크립트의 API 응답을 가로챔
    headers: dict[str, str] = field(default_factory=dict)
    origin: str | None = None           # json: 이 사이트 페이지 위에서 fetch 하기 위한 기준 주소
    capture_pattern: str | None = None  # page_capture: 가로챌 응답 URL 정규식
    fallback: "Request | None" = None   # 브라우저가 아닐 때 대신 쓸 요청(예: 직접 API)
    data: dict[str, str] = field(default_factory=dict)  # form: 사이트의 읽기 전용 폼 API


@dataclass
class SitePolicy:
    """사이트별 안전 운영 기본값. 전역 설정보다 보수적인 쪽이 적용됩니다."""
    prefer_browser: bool = False        # auto 모드에서 처음부터 브라우저 사용
    natural_page_loads: bool = False    # 페이지 부속 자원(이미지·폰트) 차단 없이 실제 사용자처럼 로드
    in_page_media: bool = False         # 미디어를 브라우저 탭에서 직접 열어 받음
    min_page_delay: float = 0.0
    page_delay_jitter: float = 0.0
    max_articles_per_run: int | None = None


class ArticleParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Site(Protocol):
    key: str
    label: str
    login_url: str
    input_hint: str
    policy: SitePolicy

    def normalize_channel_input(self, text: str) -> dict:
        """사용자 입력(URL/이름)을 채널 레코드 필드(slug, name, site_channel_id, category)로 바꿉니다. 네트워크를 쓸 수 있습니다."""

    def channel_url(self, channel: dict) -> str: ...

    def internal_article_id(self, channel: dict, remote_id: str) -> int: ...

    def internal_comment_id(self, channel: dict, remote_id: str) -> int: ...

    def list_request(self, channel: dict, page: int) -> Request: ...

    def parse_list(self, result: FetchResult, channel: dict) -> ListParse: ...

    def article_request(self, channel: dict, article: dict) -> Request: ...

    def classify(self, result: FetchResult, kind: str) -> PageState: ...

    def parse_article(self, result: FetchResult, article: dict, channel: dict, media_hosts: list[str],
                      parse_comments: bool) -> ArticleData: ...

    def media_hosts(self, configured: list[str]) -> list[str]: ...

    def session_cookie_names(self) -> tuple[str, ...]: ...
