"""미디어·원본 HTML 파일 경로 규칙."""
from __future__ import annotations

import gzip
from pathlib import Path

from .common import atomic_write_bytes, safe_filename
from .config import Settings

EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "image/avif": ".avif", "image/bmp": ".bmp", "image/svg+xml": ".svg",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
}


def guess_extension(url_path: str, content_type: str | None, sniff: bytes | None = None) -> str:
    if sniff:
        if sniff.startswith(b"\x89PNG"):
            return ".png"
        if sniff.startswith(b"\xff\xd8"):
            return ".jpg"
        if sniff.startswith(b"GIF8"):
            return ".gif"
        if sniff[:4] == b"RIFF" and sniff[8:12] == b"WEBP":
            return ".webp"
        if sniff[4:8] == b"ftyp":
            return ".mp4"
        if sniff.startswith(b"\x1a\x45\xdf\xa3"):
            return ".webm"
    if content_type:
        ext = EXT_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower())
        if ext:
            return ext
    suffix = Path(url_path).suffix.lower()
    if suffix and len(suffix) <= 5:
        return suffix
    return ".bin"


def media_dir(settings: Settings, channel_slug: str, article_id: int) -> Path:
    return settings.media_path / safe_filename(channel_slug) / str(article_id)


def media_file_path(settings: Settings, channel_slug: str, article_id: int, seq: int, sha256: str, ext: str) -> Path:
    return media_dir(settings, channel_slug, article_id) / f"{seq:02d}_{sha256[:12]}{ext}"


def raw_html_path(settings: Settings, channel_slug: str, article_id: int, stamp: str) -> Path:
    safe_stamp = stamp.replace(":", "").replace("-", "").replace("+00:00", "Z")
    return settings.raw_path / safe_filename(channel_slug) / str(article_id) / f"{safe_stamp}.html.gz"


def save_raw_html(settings: Settings, channel_slug: str, article_id: int, stamp: str, html: str) -> Path:
    return save_raw_text(settings, channel_slug, article_id, stamp, html, "html")


def save_raw_text(settings: Settings, channel_slug: str, article_id: int, stamp: str, text: str, ext: str = "html") -> Path:
    path = raw_html_path(settings, channel_slug, article_id, stamp)
    if ext != "html":
        path = path.with_name(path.name.replace(".html.gz", f".{ext}.gz"))
    atomic_write_bytes(path, gzip.compress(text.encode("utf-8")))
    return path


def load_raw_html(path: Path) -> str:
    with gzip.open(path, "rb") as f:
        return f.read().decode("utf-8")
