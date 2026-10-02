"""게시글 페이지 파서. 제목·메타·본문·미디어 참조·댓글을 추출합니다."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

from lxml import etree
from lxml import html as lxml_html

from ..common import sha256_text

_INT = re.compile(r"-?\d[\d,]*")
EXTERNAL_HOST_HINTS = ("youtube.com", "youtu.be", "twitter.com", "x.com", "twitch.tv", "vimeo.com")


@dataclass
class MediaRef:
    seq: int
    kind: str                  # image | gif | video | emoticon | external | vote
    source_key: str            # 호스트+경로(서명 제외). 동일 미디어 식별 키
    served_url: str | None
    original_url: str | None
    poster_url: str | None
    url_expires_at: str | None
    width: int | None
    height: int | None
    origin: str = "body"

    def as_dict(self) -> dict:
        return {
            "seq": self.seq, "kind": self.kind, "source_key": self.source_key, "served_url": self.served_url,
            "original_url": self.original_url, "poster_url": self.poster_url, "url_expires_at": self.url_expires_at,
            "width": self.width, "height": self.height, "origin": self.origin,
        }


@dataclass
class Comment:
    id: int
    parent_id: int | None
    author: str | None
    created_at: str | None
    body_html: str
    body_text: str
    is_deleted: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "parent_id": self.parent_id, "author": self.author, "created_at": self.created_at,
                "body_html": self.body_html, "body_text": self.body_text, "is_deleted": self.is_deleted}


@dataclass
class ArticlePage:
    id: int | None
    slug: str | None
    url: str | None
    title: str
    category: str | None
    badges: list[str]
    author: str | None
    author_type: str | None
    created_at: str | None
    edited_at: str | None
    view_count: int | None
    like_count: int | None
    dislike_count: int | None
    comment_count: int | None
    body_html: str
    body_text: str
    body_hash: str
    media: list[MediaRef] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "slug": self.slug, "url": self.url, "title": self.title, "category": self.category,
            "badges": self.badges, "author": self.author, "author_type": self.author_type, "created_at": self.created_at,
            "edited_at": self.edited_at, "view_count": self.view_count, "like_count": self.like_count,
            "dislike_count": self.dislike_count, "comment_count": self.comment_count, "body_html": self.body_html,
            "body_text": self.body_text, "body_hash": self.body_hash,
        }


class ArticleParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.text_content()).strip() if el is not None else ""


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    match = _INT.search(text)
    return int(match.group().replace(",", "")) if match else None


def normalize_time(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?$", value)
    if not match:
        return None
    base, tz = match.groups()
    return base + ("+00:00" if tz in (None, "Z") else tz)


def _absolute(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return urljoin("https://arca.live/", url)
    return url


def source_key_of(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    # 변환본(ac.arca.live)과 원본(ac-o.arca.live)은 같은 경로를 공유하므로 호스트 접두어를 정규화합니다.
    host = re.sub(r"^ac-o\.", "ac.", host)
    return f"{host}{parts.path}"


def expires_of(url: str | None) -> str | None:
    if not url:
        return None
    query = parse_qs(urlsplit(url).query)
    value = query.get("expires", [None])[0]
    if not value or not value.isdigit():
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(microsecond=0).isoformat()


def _host_allowed(url: str | None, allowed_hosts: list[str] | None) -> bool:
    if not url:
        return False
    host = (urlsplit(url).hostname or "").lower()
    if allowed_hosts is None:
        return True
    return any(host == h or host.endswith("." + h) for h in allowed_hosts) or host.endswith(".arca.live") or host.endswith(".namu.la")


def _body_text(node) -> str:
    """본문 텍스트: 블록 요소마다 줄바꿈을 넣고 공백을 정리합니다."""
    parts: list[str] = []

    def walk(el):
        if not isinstance(el.tag, str):
            return  # 주석·처리 명령 노드는 본문이 아닙니다(꼬리 텍스트는 부모가 처리)
        tag = el.tag
        if tag in ("script", "style"):
            return
        if tag == "br":
            parts.append("\n")
        if tag == "img":
            alt = el.get("alt")
            if alt and el.get("class") and "twemoji" in el.get("class"):
                parts.append(alt)
        if el.text:
            parts.append(el.text)
        for child in el:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "tr", "table"):
            parts.append("\n")

    walk(node)
    text = "".join(parts).replace("\xa0", " ").replace("​", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_media(content, allowed_hosts: list[str] | None = None, origin: str = "body") -> list[MediaRef]:
    refs: list[MediaRef] = []
    seen: set[str] = set()
    seq = 0
    for el in content.xpath('.//img|.//video|.//iframe'):
        tag = el.tag
        classes = set((el.get("class") or "").split())
        if tag == "img" and "twemoji" in classes:
            continue
        served = _absolute(el.get("src") or el.get("data-src"))
        if tag == "video" and not served:
            sources = el.xpath('./source/@src')
            served = _absolute(sources[0]) if sources else None
        original = _absolute(el.get("data-originalurl"))
        poster = _absolute(el.get("poster"))
        if tag == "iframe":
            if "arca-vote" in classes or (served and "/api/vote" in served):
                continue
            if not served:
                continue
            kind = "external"
            key = source_key_of(served)
        else:
            if not served and not original:
                continue
            if not (_host_allowed(served, allowed_hosts) or _host_allowed(original, allowed_hosts)):
                kind = "external"
            elif "arca-emoticon" in classes:
                kind = "emoticon"
            elif tag == "video":
                src_path = urlsplit(original or served or "").path.lower()
                kind = "gif" if ((el.get("data-orig") or "").lower() == "gif" or src_path.endswith(".gif")) else "video"
            else:
                kind = "image"
            key = source_key_of(original or served)
        if not key or key in seen:
            continue
        seen.add(key)
        seq += 1
        refs.append(MediaRef(
            seq=seq, kind=kind, source_key=key, served_url=served, original_url=original, poster_url=poster,
            url_expires_at=expires_of(served or original), width=_int(el.get("width")), height=_int(el.get("height")),
            origin=origin,
        ))
    return refs


def _parse_info(info_el) -> dict:
    """`.article-info`의 라벨/값 쌍을 읽습니다."""
    result: dict = {}
    if info_el is None:
        return result
    label = None
    for span in info_el.xpath('./span'):
        classes = set((span.get("class") or "").split())
        if "head" in classes:
            label = _text(span)
        elif "body" in classes and label is not None:
            result[label] = span
            label = None
        elif "date" in classes:
            heads = span.xpath('.//span[contains(@class,"head")]')
            times = span.xpath('.//time/@datetime')
            if heads and times:
                result[_text(heads[0]) + "_time"] = times[0]
            elif times:
                result["작성일_time"] = times[0]
    return result


def parse_article_page(html: str, expected_id: int | None = None, allowed_hosts: list[str] | None = None,
                       parse_comments: bool = True) -> ArticlePage:
    doc = lxml_html.fromstring(html)
    wrapper = doc.xpath('//div[contains(@class,"article-wrapper")]')
    if not wrapper:
        raise ArticleParseError("NO_ARTICLE", "게시글 영역(.article-wrapper)을 찾지 못했습니다.")
    wrapper = wrapper[0]
    warnings: list[str] = []

    link = wrapper.xpath('.//div[contains(@class,"article-link")]//a/@href')
    canonical = link[0].strip() if link else None
    if not canonical:
        canon = doc.xpath('//link[@rel="canonical"]/@href')
        canonical = canon[0].strip() if canon else None
    slug = article_id = None
    if canonical:
        m = re.search(r"/b/([^/?#]+)/(\d+)", canonical)
        if m:
            slug, article_id = m.group(1), int(m.group(2))
    if expected_id is not None and article_id is not None and article_id != expected_id:
        raise ArticleParseError("ID_MISMATCH", f"요청한 글({expected_id})과 페이지의 글({article_id})이 다릅니다.")
    if article_id is None:
        article_id = expected_id

    title_el = wrapper.xpath('.//div[contains(@class,"article-head")]//div[contains(@class,"title")]')
    title_el = title_el[0] if title_el else None
    badges = [_text(b) for b in (title_el.xpath('.//span[contains(@class,"badge")]') if title_el is not None else [])]
    badges = [b for b in badges if b]
    if title_el is not None:
        title = " ".join(t.strip() for t in title_el.xpath('./text()') if t.strip())
        if not title:
            title = _text(title_el)
            for b in badges:
                title = title.replace(b, "", 1)
        title = re.sub(r"\s+", " ", title).strip()
    else:
        title = ""
        warnings.append("TITLE_NOT_FOUND")

    author = author_type = None
    member = wrapper.xpath('.//div[contains(@class,"member-info")]//*[contains(@class,"user-info")]')
    if member:
        filt = member[0].xpath('.//*[@data-filter]')
        author = filt[0].get("data-filter").strip() if filt else (_text(member[0]) or None)
        icon = member[0].xpath('.//*[contains(@class,"user-icon")]/@title')
        author_type = icon[0].strip() if icon else None

    info = wrapper.xpath('.//div[contains(@class,"article-info")]')
    values = _parse_info(info[0] if info else None)
    like = _int(_text(values.get("추천"))) if values.get("추천") is not None else None
    dislike = _int(_text(values.get("비추천"))) if values.get("비추천") is not None else None
    views = _int(_text(values.get("조회수"))) if values.get("조회수") is not None else None
    comment_count = _int(_text(values.get("댓글"))) if values.get("댓글") is not None else None
    created_at = normalize_time(values.get("작성일_time"))
    edited_at = normalize_time(values.get("수정일_time")) if values.get("수정일_time") else None
    if created_at is None:
        any_time = wrapper.xpath('.//div[contains(@class,"article-head")]//time/@datetime')
        created_at = normalize_time(any_time[0]) if any_time else None
        if created_at is None:
            warnings.append("CREATED_AT_NOT_FOUND")

    content = wrapper.xpath('.//div[contains(@class,"article-body")]//div[contains(@class,"article-content")]')
    if not content:
        raise ArticleParseError("NO_CONTENT", "본문 영역(.article-content)을 찾지 못했습니다.")
    content = content[0]
    for ad in content.xpath('.//div[contains(@class,"ad")][@data-ad-t]'):
        ad.drop_tree()
    body_html = "".join(
        etree.tostring(child, encoding="unicode", method="html") if isinstance(child.tag, str) else (child.text or "")
        for child in content
    )
    if content.text:
        body_html = content.text + body_html
    body_html = body_html.strip()
    body_text = _body_text(content)
    media = extract_media(content, allowed_hosts)
    body_hash = sha256_text(title + "\n" + body_text + "\n" + "|".join(m.source_key for m in media))

    comments: list[Comment] = []
    if parse_comments:
        comments = parse_comments_block(wrapper)
        if comment_count is None:
            tcc = wrapper.xpath('.//span[contains(@class,"title-comment-count")]')
            comment_count = _int(_text(tcc[0])) if tcc else None

    return ArticlePage(
        id=article_id, slug=slug, url=canonical, title=title, category=badges[0] if badges else None, badges=badges,
        author=author, author_type=author_type, created_at=created_at, edited_at=edited_at, view_count=views,
        like_count=like, dislike_count=dislike, comment_count=comment_count, body_html=body_html, body_text=body_text,
        body_hash=body_hash, media=media, comments=comments, warnings=warnings,
    )


def parse_comments_block(wrapper) -> list[Comment]:
    comments: list[Comment] = []
    area = wrapper.xpath('.//div[contains(@class,"article-comment")]//div[contains(@class,"list-area")]')
    if not area:
        return comments
    last_top_level: int | None = None
    for item in area[0].xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " comment-item ")][@id]'):
        cid = _int(item.get("id") or "")
        if cid is None:
            continue
        classes = set((item.get("class") or "").split())
        wrapper = item.getparent()
        wrapper_classes = set((wrapper.get("class") or "").split()) if wrapper is not None else set()
        parent_id = _int(item.get("data-parent") or item.get("data-parent-id") or "")
        if parent_id is None:
            # 답글은 상위 wrapper 안에 중첩되거나 reply 클래스로 표시됩니다.
            ancestor = wrapper.getparent() if wrapper is not None else None
            while ancestor is not None and ancestor is not area[0]:
                if "comment-wrapper" in set((ancestor.get("class") or "").split()):
                    inner = ancestor.xpath('./div[contains(@class,"comment-item")][@id]')
                    parent_id = _int(inner[0].get("id") or "") if inner else None
                    break
                ancestor = ancestor.getparent()
        is_reply = any("reply" in c for c in classes | wrapper_classes) or parent_id is not None
        if parent_id is None and is_reply:
            parent_id = last_top_level
        if not is_reply:
            last_top_level = cid
        author_el = item.xpath('.//div[contains(@class,"info-row")]//*[@data-filter]')
        author = author_el[0].get("data-filter").strip() if author_el else None
        time_el = item.xpath('.//div[contains(@class,"info-row")]//time/@datetime')
        created_at = normalize_time(time_el[0]) if time_el else None
        message = item.xpath('./div[contains(@class,"content")]/div[contains(@class,"message")]') or item.xpath('.//div[contains(@class,"message")]')
        if message:
            node = message[0]
            # 사이트 UI 요소(펼쳐보기 버튼 등)는 댓글 내용이 아니므로 제거합니다.
            for ui in node.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " btn ")'
                                 ' or contains(@class, "btn-more") or contains(@class, "comment-more")]'):
                ui.drop_tree()
            body_html = "".join(etree.tostring(c, encoding="unicode", method="html") for c in node if isinstance(c.tag, str)).strip()
            body_text = _body_text(node)
        else:
            body_html, body_text = "", ""
        is_deleted = "삭제된 댓글" in body_text or "deleted" in classes
        comments.append(Comment(id=cid, parent_id=parent_id, author=author, created_at=created_at,
                                body_html=body_html, body_text=body_text, is_deleted=is_deleted))
    return comments
