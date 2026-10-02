"""httpx 기반 가져오기. 공개 채널용이며 Cloudflare 확인 화면은 상태 분류기에서 감지해 브라우저로 넘깁니다."""
from __future__ import annotations

import httpx

from ..common import AppError
from ..config import Settings
from .base import DownloadResult, FetchResult

_PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}


class HttpFetcher:
    name = "http"

    def __init__(self, settings: Settings):
        from .browser_fetcher import cached_user_agent

        self.settings = settings
        self.client = httpx.Client(
            headers={"User-Agent": cached_user_agent(settings) or settings.http.user_agent, **_PAGE_HEADERS},
            timeout=httpx.Timeout(settings.http.timeout_seconds, connect=settings.media.original_connect_timeout_seconds),
            follow_redirects=True,
        )
        self.stop_event = None  # 실행 컨텍스트가 넣어 주는 중지 이벤트(선택)

    def fetch_page(self, url: str) -> FetchResult:
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise AppError("NETWORK_ERROR", f"페이지 요청에 실패했습니다: {type(exc).__name__}") from None
        return FetchResult(url=url, status=response.status_code, html=response.text, final_url=str(response.url),
                           headers={k.lower(): v for k, v in response.headers.items()}, fetcher=self.name)

    def fetch_api(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        """JSON API 호출. 응답 본문은 FetchResult.html 에 문자열로 담깁니다."""
        try:
            response = self.client.get(url, headers={"Accept": "application/json, text/plain, */*", **(headers or {})})
        except httpx.HTTPError as exc:
            raise AppError("NETWORK_ERROR", f"API 요청에 실패했습니다: {type(exc).__name__}") from None
        return FetchResult(url=url, status=response.status_code, html=response.text, final_url=str(response.url),
                           headers={k.lower(): v for k, v in response.headers.items()}, fetcher=self.name)

    def download(self, url: str, referer: str, max_bytes: int) -> DownloadResult:
        from .download import stream_download

        return stream_download(self.client, url, referer, max_bytes, stop_event=self.stop_event,
                               resume_path=getattr(self, "download_resume_path", None),
                               max_duration_seconds=getattr(self, "download_timeout_seconds", self.settings.media.max_download_seconds))

    def fetch_form(self, url: str, data: dict, headers: dict) -> FetchResult:
        try:
            response = self.client.post(url, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise AppError('NETWORK_ERROR', f'댓글 요청 실패: {type(exc).__name__}') from None
        return FetchResult(url=url, status=response.status_code, html=response.text, final_url=str(response.url),
                           headers=dict(response.headers), fetcher=self.name)

    def close(self) -> None:
        self.client.close()
