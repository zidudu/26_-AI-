"""가져오기 계층 공통 타입. HTTP/브라우저 구현이 같은 인터페이스를 제공합니다."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class FetchResult:
    url: str
    status: int
    html: str = ""
    final_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    fetcher: str = ""  # http | browser

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class DownloadResult:
    url: str
    status: int
    data: bytes = b""
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and bool(self.data)


class Fetcher(Protocol):
    name: str

    def fetch_page(self, url: str) -> FetchResult: ...

    def fetch_api(self, url: str, headers: dict[str, str] | None = None) -> FetchResult: ...

    def download(self, url: str, referer: str, max_bytes: int) -> DownloadResult: ...

    def close(self) -> None: ...
