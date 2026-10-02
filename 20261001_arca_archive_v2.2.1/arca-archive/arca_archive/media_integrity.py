"""Reject known original-format contradictions using file bytes, not HTTP labels.

Matching formats alone do not prove byte identity with an uploader's original.
Unknown formats are left unchanged; this guard does not guess from MIME headers.
"""
from pathlib import Path
from urllib.parse import urlsplit


FORMAT_MIME = {".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg",
               ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
               ".avif": "image/avif"}


def detected_format(data: bytes) -> str | None:
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp":
        # AVIF shares the ISO BMFF container signature with MP4.
        brands = [data[8:12], *[data[i:i + 4] for i in range(16, min(len(data), 64), 4)]]
        return ".avif" if any(b in (b"avif", b"avis") for b in brands) else ".mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm"
    return None


def original_format_mismatch(media: dict, data: bytes) -> tuple[str, str] | None:
    expected = Path(urlsplit(media.get("original_url") or media.get("source_key") or "").path).suffix.lower()
    expected = {".jpeg": ".jpg", ".m4v": ".mp4", ".mov": ".mp4"}.get(expected, expected)
    actual = detected_format(data)
    if expected in FORMAT_MIME and actual and actual != expected:
        return expected, actual
    return None
