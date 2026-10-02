"""채널 목록 페이지 파서. 순수 함수이며 네트워크를 사용하지 않습니다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import html as lxml_html

from .page_state import has_sensitive_media_flag, is_logged_in

_ARTICLE_HREF = re.compile(r"^(?:https?://arca\.live)?/b/([^/?#]+)/(\d+)(?:[?#].*)?$")
_INT = re.compile(r"-?\d+")


@dataclass
class ListRow:
    id: int
    url: str
    title: str
    category: str | None
    badges: list[str]
    author: str | None
    created_at: str | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    channel_local_no: int | None
    is_notice: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title, "category": self.category, "badges": self.badges,
            "author": self.author, "created_at": self.created_at, "view_count": self.view_count,
            "like_count": self.like_count, "comment_count": self.comment_count,
            "channel_local_no": self.channel_local_no, "is_notice": self.is_notice,
        }


@dataclass
class ListPage:
    slug: str
    rows: list[ListRow] = field(default_factory=list)
    notice_rows: int = 0
    skipped_rows: int = 0
    page_numbers: list[int] = field(default_factory=list)
    has_next: bool = False
    categories: list[tuple[str, str]] = field(default_factory=list)  # (표시명, category 파라미터)
    channel_name: str | None = None
    logged_in: bool | None = None
    sensitive_channel: bool = False

    @property
    def hidden_for_anonymous(self) -> bool:
        """민감 채널에서 비로그인 세션이면 일반 글이 숨겨집니다."""
        normal_rows = [r for r in self.rows if not r.is_notice]
        return self.sensitive_channel and self.logged_in is False and not normal_rows and self.notice_rows > 0


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip() if el is not None else ""


def _int(text: str | None) -> int | None:
    if not text:
        return None
    match = _INT.search(text.replace(",", ""))
    return int(match.group()) if match else None


def _normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$", value)
    if not match:
        return None
    base, tz = match.groups()
    if tz in (None, "Z"):
        return base + "+00:00"
    return base + tz


def parse_list_page(html: str, slug: str) -> ListPage:
    doc = lxml_html.fromstring(html)
    page = ListPage(slug=slug)
    page.logged_in = is_logged_in(html)
    page.sensitive_channel = has_sensitive_media_flag(html)

    name_el = doc.xpath('//meta[@name="title"]/@content')
    if name_el:
        page.channel_name = name_el[0].strip() or None

    for a in doc.xpath('//a[contains(concat(" ", normalize-space(@class), " "), " vrow ")]'):
        classes = set((a.get("class") or "").split())
        if "head" in classes or "notice-unfilter" in classes:
            continue
        href = a.get("href") or ""
        match = _ARTICLE_HREF.match(href)
        if not match or match.group(1) != slug:
            page.skipped_rows += 1
            continue
        is_notice = "notice" in classes
        if "notice-service" in classes:
            # 사이트 전체 공지/광고는 채널 글이 아닙니다.
            page.skipped_rows += 1
            continue
        if is_notice:
            page.notice_rows += 1
        article_id = int(match.group(2))
        title_el = a.xpath('.//span[contains(@class,"col-title")]')
        title_el = title_el[0] if title_el else None
        badges = [_text(b) for b in (title_el.xpath('.//span[contains(@class,"badges")]//span[contains(@class,"badge")]') if title_el is not None else [])]
        badges = [b for b in badges if b]
        title = ""
        if title_el is not None:
            inner = title_el.xpath('.//span[contains(concat(" ", normalize-space(@class), " "), " title ")]')
            if inner:
                node = inner[0]
                # 제목 안의 댓글 수 [N] 표시는 제외합니다.
                title = " ".join(
                    t.strip() for t in node.xpath('./text()') + node.xpath('.//*[not(contains(@class,"comment-count"))]/text()')
                )
                if not title.strip():
                    title = _text(node)
            else:
                title = _text(title_el.xpath('./b')[0]) if title_el.xpath('./b') else _text(title_el)
            title = re.sub(r"\s+", " ", title).strip()
            for b in badges:
                if title.startswith(b + " "):
                    title = title[len(b) + 1:]
        comment_count = None
        cc = a.xpath('.//span[contains(@class,"comment-count")]')
        if cc:
            comment_count = _int(_text(cc[0]))
        author_el = a.xpath('.//span[contains(@class,"col-author")]//*[@data-filter]')
        author = author_el[0].get("data-filter").strip() if author_el else (
            _text(a.xpath('.//span[contains(@class,"col-author")]')[0]) if a.xpath('.//span[contains(@class,"col-author")]') else None)
        time_el = a.xpath('.//span[contains(@class,"col-time")]//time/@datetime')
        created_at = _normalize_time(time_el[0]) if time_el else None
        view_el = a.xpath('.//span[contains(@class,"col-view")]')
        rate_el = a.xpath('.//span[contains(@class,"col-rate")]')
        id_el = a.xpath('.//span[contains(@class,"col-id")]')
        local_no = _int(_text(id_el[0])) if id_el else None
        page.rows.append(ListRow(
            id=article_id,
            url=f"https://arca.live/b/{slug}/{article_id}",
            title=title,
            category=badges[0] if badges else None,
            badges=badges,
            author=author or None,
            created_at=created_at,
            view_count=_int(_text(view_el[0])) if view_el else None,
            like_count=_int(_text(rate_el[0])) if rate_el else None,
            comment_count=comment_count,
            channel_local_no=local_no if (local_no is not None and not is_notice) else None,
            is_notice=is_notice,
        ))

    numbers = []
    for link in doc.xpath('//nav[contains(@class,"pagination-wrapper")]//a[contains(@class,"page-link")]'):
        href = link.get("href") or ""
        m = re.search(r"[?&]p=(\d+)", href)
        if m:
            numbers.append(int(m.group(1)))
        if link.xpath('.//*[contains(@class,"chevron-right")]'):
            page.has_next = True
    page.page_numbers = sorted(set(numbers))
    active = doc.xpath('//li[contains(@class,"active")]/a[contains(@class,"page-link")]/@href')
    current = None
    if active:
        m = re.search(r"[?&]p=(\d+)", active[0])
        current = int(m.group(1)) if m else 1
    if current is not None and any(n > current for n in page.page_numbers):
        page.has_next = True

    for link in doc.xpath('//div[contains(@class,"board-category")]//a[@href]'):
        href = link.get("href") or ""
        m = re.search(r"[?&]category=([^&#]+)", href)
        label = _text(link)
        if m and label:
            page.categories.append((label, m.group(1)))
    return page
