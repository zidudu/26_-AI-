"""Playwright + 시스템 Chrome(전용 프로필) 기반 가져오기.

- 프로필은 `data/browser_profile`에 유지되어 사용자가 한 번 로그인하면 세션이 이어집니다.
- 자격증명은 프로그램이 다루지 않습니다. 로그인은 `open_login_window()`가 띄운 창에서 사용자가 직접 합니다.
- UA는 실제 Chrome 버전에 맞춰 `HeadlessChrome` 표기만 `Chrome`으로 바꿉니다(클라이언트 힌트와 버전 불일치 방지).
- API 호출(`fetch_api`)은 브라우저 페이지 안의 `fetch`로 보내 실제 브라우저의 TLS·헤더·쿠키를 그대로 씁니다.
- `fetch_page_capture`는 실제 페이지를 열고 그 페이지의 스크립트가 호출한 API 응답을 가로챕니다(사람이 읽는 것과 같은 트래픽).
- 미디어는 사이트 설정에 따라 httpx 스트리밍(서명 URL만 검사하는 CDN) 또는 브라우저 탭 탐색(`in_page_downloads`)으로 받습니다.
"""
from __future__ import annotations

import json
import re
import time

from ..common import AppError, atomic_write_json, read_json
from ..config import Settings
from .base import DownloadResult, FetchResult

_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
_PROFILE_IN_USE_HINTS = ("ProcessSingleton", "profile", "already running", "Target page, context or browser has been closed")


def ua_cache_path(settings: Settings):
    return settings.data_path / "browser_ua.json"


def cached_user_agent(settings: Settings) -> str | None:
    """이전 실행에서 확인한 실제 Chrome UA(HeadlessChrome 표기 제거본)."""
    try:
        data = read_json(ua_cache_path(settings))
        ua = data.get("ua")
        return ua if isinstance(ua, str) and "Mozilla/" in ua else None
    except (OSError, ValueError, AttributeError):
        return None


def _launch_options(settings: Settings, headless: bool, user_agent: str | None = None) -> dict:
    options = {
        "user_data_dir": str(settings.profile_path),
        "headless": headless,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "viewport": {"width": settings.browser.viewport_width, "height": settings.browser.viewport_height},
        "args": ["--disable-blink-features=AutomationControlled"],
        "ignore_default_args": ["--enable-automation"],
    }
    if user_agent:
        options["user_agent"] = user_agent
    if settings.browser.channel != "chromium":
        options["channel"] = settings.browser.channel
    return options


class BrowserFetcher:
    name = "browser"

    def __init__(self, settings: Settings, headless: bool | None = None, block_resources: bool = True):
        self.settings = settings
        self.headless = settings.browser.headless if headless is None else headless
        self.block_resources = block_resources
        self.in_page_downloads = False   # True면 미디어를 브라우저 탭 탐색으로 받습니다(사이트별 설정)
        self._pw = None
        self._context = None
        self._page = None
        self._media_page = None
        self._media_client = None
        self.user_agent: str | None = None
        self.stop_event = None  # 실행 컨텍스트가 넣어 주는 중지 이벤트(선택)

    # ------------------------------------------------------------------ 수명
    def start(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.sync_api import Error as PlaywrightError, sync_playwright
        except ImportError:
            raise AppError("PLAYWRIGHT_MISSING", "playwright 패키지가 없습니다. 의존성을 설치하세요.", stop=True, retryable=False) from None
        self.settings.profile_path.mkdir(parents=True, exist_ok=True)
        if login_window_locked(self.settings):
            raise AppError("BROWSER_PROFILE_IN_USE", "로그인 창이 열려 있어 같은 프로필을 사용할 수 없습니다. 로그인 창을 닫은 뒤 다시 실행하세요.",
                           stop=True)
        self._pw = sync_playwright().start()
        try:
            self._launch(cached_user_agent(self.settings))
            fixed = self._resolve_user_agent()
            if fixed and fixed != self.user_agent:
                # 실제 Chrome 버전에 맞는 UA로 다시 띄웁니다(최초 1회 또는 Chrome 업데이트 후).
                self._context.close()
                self._launch(fixed)
        except PlaywrightError as exc:
            self._pw.stop()
            self._pw = None
            text = str(exc)
            if any(hint in text for hint in _PROFILE_IN_USE_HINTS):
                raise AppError("BROWSER_PROFILE_IN_USE",
                               "브라우저 프로필을 다른 창이 사용 중입니다. 로그인 창을 닫은 뒤 다시 실행하세요.",
                               stop=True) from None
            raise AppError("BROWSER_START_FAILED",
                           f"브라우저를 시작하지 못했습니다({self.settings.browser.channel}). Chrome 설치와 설정을 확인하세요.",
                           stop=True) from None
        self._context.set_default_navigation_timeout(self.settings.browser.navigation_timeout_seconds * 1000)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        if self.block_resources:
            self._page.route("**/*", self._route)

    def _launch(self, user_agent: str | None) -> None:
        self._context = self._pw.chromium.launch_persistent_context(**_launch_options(self.settings, self.headless, user_agent))
        self.user_agent = user_agent

    def _resolve_user_agent(self) -> str | None:
        """실제 브라우저 버전과 일치하는 UA를 정하고 캐시합니다. 변경이 필요 없으면 현재 값을 돌려줍니다."""
        try:
            page = self._context.pages[0] if self._context.pages else self._context.new_page()
            real = page.evaluate("navigator.userAgent") or ""
        except Exception:
            return self.user_agent
        version = None
        try:
            browser = self._context.browser
            version = browser.version if browser else None
        except Exception:
            version = None
        major = version.split(".")[0] if version else None
        if self.user_agent is None:
            fixed = real.replace("HeadlessChrome", "Chrome")
        else:
            fixed = self.user_agent
            if major and not re.search(rf"Chrome/{major}\.", fixed):
                fixed = re.sub(r"Chrome/\d+(\.\d+)*", f"Chrome/{major}.0.0.0", fixed)
        fixed = fixed.replace("HeadlessChrome", "Chrome")
        if fixed != cached_user_agent(self.settings):
            try:
                atomic_write_json(ua_cache_path(self.settings), {"ua": fixed, "browser_version": version})
            except OSError:
                pass
        return fixed

    @staticmethod
    def _route(route, request) -> None:
        if request.resource_type in _BLOCKED_RESOURCE_TYPES:
            route.abort()
        else:
            route.continue_()

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        finally:
            self._context = None
            self._page = None
            self._media_page = None
            if self._media_client is not None:
                try:
                    self._media_client.close()
                except Exception:
                    pass
                self._media_client = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:
                    pass
                self._pw = None

    # ------------------------------------------------------------------ 페이지
    def fetch_page(self, url: str) -> FetchResult:
        self.start()
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

        page = self._page
        try:
            response = page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeout:
            raise AppError("NETWORK_TIMEOUT", "페이지 로딩이 제한 시간을 넘었습니다.") from None
        except PlaywrightError as exc:
            text = str(exc)
            if "closed" in text:
                self.close()
                raise AppError("BROWSER_CLOSED", "브라우저가 닫혔습니다. 다음 실행에서 다시 시작합니다.", stop=True) from None
            raise AppError("NETWORK_ERROR", f"페이지 요청에 실패했습니다: {type(exc).__name__}") from None
        status = response.status if response is not None else 0
        headers = {k.lower(): v for k, v in (response.headers if response is not None else {}).items()}
        if self._is_challenge(page.title(), headers):
            resolved = self._wait_challenge(page)
            if resolved:
                status = 200
                headers.pop("cf-mitigated", None)
        try:
            html = page.content()
        except PlaywrightError:
            raise AppError("NETWORK_ERROR", "페이지 내용을 읽지 못했습니다.") from None
        return FetchResult(url=url, status=status, html=html, final_url=page.url, headers=headers, fetcher=self.name)

    def fetch_page_capture(self, url: str, pattern: str, session_cookies: tuple[str, ...] = (),
                           timeout_seconds: float | None = None) -> FetchResult:
        """실제 페이지를 열고, 그 페이지의 스크립트가 호출한 API 응답(URL이 pattern 정규식에 맞는 것)을 가로챕니다.

        가로채지 못하면 status=0 과 페이지 HTML을 돌려주고 headers['x-capture']='timeout' 을 표시합니다.
        headers['x-has-session'] 은 지정한 세션 쿠키가 프로필에 있는지(1/0)입니다.
        """
        self.start()
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

        page = self._page
        regex = re.compile(pattern)
        timeout = (timeout_seconds or self.settings.browser.navigation_timeout_seconds) * 1000
        has_session = "1" if all(self.cookies_for_names(session_cookies)) else "0"
        captured = None
        try:
            with page.expect_response(lambda r: bool(regex.search(r.url)), timeout=timeout) as info:
                page.goto(url, wait_until="domcontentloaded")
            captured = info.value
        except PlaywrightTimeout:
            captured = None
        except PlaywrightError as exc:
            text = str(exc)
            if "closed" in text:
                self.close()
                raise AppError("BROWSER_CLOSED", "브라우저가 닫혔습니다. 다음 실행에서 다시 시작합니다.", stop=True) from None
            raise AppError("NETWORK_ERROR", f"페이지 요청에 실패했습니다: {type(exc).__name__}") from None
        if captured is None:
            try:
                html = page.content()
            except PlaywrightError:
                html = ""
            return FetchResult(url=url, status=0, html=html, final_url=page.url,
                               headers={"x-capture": "timeout", "x-has-session": has_session}, fetcher=self.name)
        try:
            body = captured.text()
        except PlaywrightError:
            body = ""
        headers = {k.lower(): v for k, v in captured.headers.items()}
        headers.update({"x-capture": "ok", "x-captured-url": captured.url, "x-has-session": has_session})
        return FetchResult(url=url, status=captured.status, html=body, final_url=page.url, headers=headers, fetcher=self.name)

    @staticmethod
    def _is_challenge(title: str, headers: dict[str, str]) -> bool:
        return headers.get("cf-mitigated") == "challenge" or "Just a moment" in (title or "")

    def _wait_challenge(self, page) -> bool:
        deadline = time.monotonic() + self.settings.browser.challenge_wait_seconds
        while time.monotonic() < deadline:
            if self.stop_event is not None and self.stop_event.is_set():
                return False
            try:
                if "Just a moment" not in page.title():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    # ------------------------------------------------------------------ API
    def fetch_api(self, url: str, headers: dict[str, str] | None = None, origin: str | None = None) -> FetchResult:
        """브라우저 페이지 안의 fetch 로 API를 호출합니다(실제 브라우저의 TLS·헤더·쿠키).

        origin 을 주면 그 사이트의 페이지 위에서 호출합니다(같은 사이트 쿠키·CORS 조건을 맞추기 위해).
        페이지 fetch 가 실패(CORS 등)하면 컨텍스트 요청 API로 대체하고 headers['x-fetch-mode']='context' 로 표시합니다.
        """
        self.start()
        from urllib.parse import urlsplit

        from playwright.sync_api import Error as PlaywrightError

        page = self._page
        if origin:
            want = (urlsplit(origin).hostname or "").lower()
            current = (urlsplit(page.url).hostname or "").lower()
            if not current or not (current == want or current.endswith("." + want.split(".", 1)[-1])):
                try:
                    page.goto(origin, wait_until="domcontentloaded")
                except PlaywrightError as exc:
                    raise AppError("NETWORK_ERROR", f"페이지 이동에 실패했습니다: {type(exc).__name__}") from None
        script = """async ([u, h]) => {
            try {
                const r = await fetch(u, {credentials: 'include', headers: h});
                const t = await r.text();
                return {ok: true, status: r.status, url: r.url, text: t, ct: r.headers.get('content-type') || ''};
            } catch (e) { return {ok: false, error: String(e)}; }
        }"""
        try:
            data = page.evaluate(script, [url, headers or {}])
        except PlaywrightError as exc:
            raise AppError("NETWORK_ERROR", f"페이지 내 API 호출에 실패했습니다: {type(exc).__name__}") from None
        if data.get("ok"):
            return FetchResult(url=url, status=int(data["status"]), html=data.get("text") or "", final_url=data.get("url") or url,
                               headers={"content-type": data.get("ct") or "", "x-fetch-mode": "inpage"}, fetcher=self.name)
        # CORS 등으로 페이지 fetch 가 막힌 경우의 대체 경로(브라우저 지문은 아님)
        try:
            response = self._context.request.get(url, headers={"Accept": "application/json, text/plain, */*", **(headers or {})},
                                                 timeout=self.settings.http.timeout_seconds * 1000, max_redirects=5)
            text = response.text()
            result = FetchResult(url=url, status=response.status, html=text, final_url=response.url,
                                 headers={k.lower(): v for k, v in response.headers.items()} | {"x-fetch-mode": "context",
                                                                                                   "x-inpage-error": str(data.get("error"))[:120]},
                                 fetcher=self.name)
            response.dispose()
            return result
        except PlaywrightError as exc:
            raise AppError("NETWORK_ERROR", f"API 요청에 실패했습니다: {type(exc).__name__}") from None

    def cookies_for(self, host_suffix: str) -> dict[str, str]:
        self.start()
        out: dict[str, str] = {}
        try:
            for c in self._context.cookies():
                domain = (c.get("domain") or "").lstrip(".").lower()
                if domain.endswith(host_suffix):
                    out[c["name"]] = c["value"]
        except Exception:
            pass
        return out

    def cookies_for_names(self, names: tuple[str, ...]) -> list[bool]:
        if not names:
            return [True]
        try:
            present = {c["name"] for c in self._context.cookies() if c.get("value")}
        except Exception:
            present = set()
        return [n in present for n in names]

    # ------------------------------------------------------------------ 다운로드
    def fetch_form(self, url: str, data: dict, headers: dict) -> FetchResult:
        self.start()
        from playwright.sync_api import Error as PlaywrightError
        try:
            response = self._context.request.post(url, form=data, headers=headers,
                                                   timeout=self.settings.http.timeout_seconds * 1000)
            return FetchResult(url=url, status=response.status, html=response.text(), final_url=response.url,
                               headers=response.headers, fetcher=self.name)
        except PlaywrightError as exc:
            raise AppError('NETWORK_ERROR', f'댓글 요청 실패: {type(exc).__name__}') from None

    def download(self, url: str, referer: str, max_bytes: int) -> DownloadResult:
        if self.in_page_downloads:
            return self._download_via_tab(url, max_bytes)
        return self._download_via_http(url, referer, max_bytes)

    def _download_via_http(self, url: str, referer: str, max_bytes: int) -> DownloadResult:
        """CDN은 서명 URL만 검사하므로 httpx 스트리밍으로 받되, 브라우저 쿠키는 같은 도메인에 한해 함께 보냅니다."""
        from urllib.parse import urlsplit

        from .download import make_media_client, stream_download

        self.start()
        if self._media_client is None:
            self._media_client = make_media_client(self.user_agent or self.settings.http.user_agent, self.settings.http.timeout_seconds,
                                                   self.settings.media.original_connect_timeout_seconds)
        host = (urlsplit(url).hostname or "").lower()
        cookies: dict[str, str] = {}
        try:
            for c in self._context.cookies():
                domain = (c.get("domain") or "").lstrip(".").lower()
                if domain and (host == domain or host.endswith("." + domain)):
                    cookies[c["name"]] = c["value"]
        except Exception:
            cookies = {}
        return stream_download(self._media_client, url, referer, max_bytes, stop_event=self.stop_event,
                               resume_path=getattr(self, "download_resume_path", None),
                               cookies=cookies or None,
                               max_duration_seconds=getattr(self, "download_timeout_seconds", self.settings.media.max_download_seconds))

    def _download_via_tab(self, url: str, max_bytes: int) -> DownloadResult:
        """별도 탭에서 파일 URL을 직접 열어 받습니다. 사용자가 이미지를 새 탭에서 여는 것과 같은 요청입니다."""
        self.start()
        from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

        if self._media_page is None or self._media_page.is_closed():
            self._media_page = self._context.new_page()
        page = self._media_page
        try:
            response = page.goto(url, wait_until="commit", timeout=self.settings.http.timeout_seconds * 1000)
        except PlaywrightTimeout:
            raise AppError("NETWORK_TIMEOUT", "미디어 로딩이 제한 시간을 넘었습니다.") from None
        except PlaywrightError as exc:
            raise AppError("NETWORK_ERROR", f"미디어 요청에 실패했습니다: {type(exc).__name__}") from None
        if response is None:
            raise AppError("NETWORK_ERROR", "미디어 응답이 없습니다.")
        headers = {k.lower(): v for k, v in response.headers.items()}
        length = headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise AppError("MEDIA_TOO_LARGE", f"파일 크기({int(length):,} bytes)가 한도를 넘습니다.", retryable=False)
        if self.stop_event is not None and self.stop_event.is_set():
            raise AppError("STOPPED", "중지 요청으로 다운로드를 끊었습니다.")
        try:
            data = response.body() if response.ok else b""
        except PlaywrightError:
            raise AppError("NETWORK_ERROR", "미디어 본문을 읽지 못했습니다.") from None
        if len(data) > max_bytes:
            raise AppError("MEDIA_TOO_LARGE", "파일 크기가 한도를 넘습니다.", retryable=False)
        return DownloadResult(url=url, status=response.status, data=data, content_type=headers.get("content-type"), headers=headers)


_BROWSER_CANDIDATES = {
    "chrome": (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "%LOCALAPPDATA%\\Google\\Chrome\\Application\\chrome.exe",
    ),
    "msedge": (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ),
}


def find_browser_executable(settings: Settings):
    """로그인 창용 일반 브라우저 실행 파일을 찾습니다. 없으면 None."""
    import os
    from pathlib import Path

    for candidate in _BROWSER_CANDIDATES.get(settings.browser.channel, ()):
        path = Path(os.path.expandvars(candidate))
        if path.is_file():
            return path
    if settings.browser.channel == "chromium":
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as pw:
                path = Path(pw.chromium.executable_path)
                return path if path.is_file() else None
        except Exception:
            return None
    return None


def open_login_window(settings: Settings, start_url: str = "https://arca.live/") -> str:
    """사용자가 직접 로그인할 수 있는 보이는 창을 띄우고, 창이 닫힐 때까지 기다립니다.

    자동화 도구로 띄운 브라우저에서는 로그인 캡챠 위젯이 표시되지 않으므로, 브라우저를 자동화 없이
    일반 실행하고 전용 프로필 폴더만 공유합니다. 실행 파일을 못 찾을 때만 Playwright 창으로 대체합니다.
    """
    settings.profile_path.mkdir(parents=True, exist_ok=True)
    # 같은 프로필로 로그인 창을 두 번 열면 Chrome이 기존 창에 빈 탭만 추가하므로 잠금으로 중복 실행을 막습니다.
    from ..common import RunLock

    lock = RunLock(login_lock_path(settings))
    if not lock.acquire():
        raise AppError("LOGIN_WINDOW_OPEN", "로그인 창이 이미 열려 있습니다. 그 창의 '아카라이브' 탭에서 로그인하세요.",
                       stop=True, retryable=False)
    try:
        executable = find_browser_executable(settings)
        if executable is not None:
            return _open_plain_browser(settings, executable, start_url)
        return _open_playwright_window(settings, start_url)
    finally:
        lock.release()


def _open_plain_browser(settings: Settings, executable, start_url: str) -> str:
    import subprocess

    command = [str(executable), f"--user-data-dir={settings.profile_path}", "--profile-directory=Default",
               "--no-first-run", "--no-default-browser-check", "--disable-sync", "--new-window", start_url]
    try:
        process = subprocess.Popen(command)
    except OSError as exc:
        raise AppError("BROWSER_START_FAILED", f"브라우저를 시작하지 못했습니다: {type(exc).__name__}", stop=True) from None
    # 같은 프로필을 다른 Chrome 인스턴스가 쓰고 있으면 요청만 넘기고 곧바로 종료됩니다.
    time.sleep(2.0)
    if process.poll() is not None:
        raise AppError("BROWSER_PROFILE_IN_USE",
                       "브라우저가 곧바로 종료되었습니다. 같은 프로필을 사용하는 수집·세션 확인이 끝난 뒤 다시 시도하세요.",
                       stop=True)
    process.wait()
    return "closed"


def _open_playwright_window(settings: Settings, start_url: str) -> str:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(**_launch_options(settings, headless=False))
        except PlaywrightError as exc:
            if any(hint in str(exc) for hint in _PROFILE_IN_USE_HINTS):
                raise AppError("BROWSER_PROFILE_IN_USE", "브라우저 프로필을 다른 창(수집 또는 세션 확인)이 사용 중입니다.",
                               stop=True) from None
            raise AppError("BROWSER_START_FAILED", "브라우저를 시작하지 못했습니다.", stop=True) from None
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(start_url, wait_until="domcontentloaded")
        except PlaywrightError:
            pass
        # 시작 시 함께 열린 빈 탭은 닫아 로그인 탭만 남깁니다.
        for extra in list(context.pages):
            if extra is not page and extra.url in ("about:blank", ""):
                try:
                    extra.close()
                except PlaywrightError:
                    pass
        # 사용자가 모든 탭/창을 닫으면 종료합니다. 브라우저 자체가 닫히면 close 이벤트로 빠져나옵니다.
        closed = {"flag": False}
        context.on("close", lambda *_: closed.__setitem__("flag", True))
        try:
            while not closed["flag"] and context.pages:
                time.sleep(0.5)
        except PlaywrightError:
            pass
        try:
            context.close()
        except PlaywrightError:
            pass
    return "closed"


def login_lock_path(settings: Settings):
    return settings.data_path / "login_window.lock"


def login_window_locked(settings: Settings) -> bool:
    """다른 프로세스(GUI 버튼 또는 login.bat)가 로그인 창을 열어 두었는지 확인합니다."""
    from ..common import RunLock

    probe = RunLock(login_lock_path(settings))
    if probe.acquire():
        probe.release()
        return False
    return True


def check_session(settings: Settings, site: str = "arca") -> dict:
    if site == 'dcinside':
        return {'status': None, 'logged_in': None, 'nickname': None,
                'note': '현재 디시 수집은 공개 마이너 갤러리를 대상으로 하며 로그인 상태 자동 판정은 지원하지 않습니다.'}
    """헤드리스로 세션 상태를 확인합니다. 사이트별로 로그인 여부(와 닉네임)를 돌려줍니다."""
    from ..parsers.page_state import is_logged_in

    fetcher = BrowserFetcher(settings, headless=True)
    try:
        if site == "naver_cafe":
            cookies = fetcher.cookies_for("naver.com")
            has_auth = bool(cookies.get("NID_AUT")) and bool(cookies.get("NID_SES"))
            note = "네이버 로그인 쿠키(NID_AUT/NID_SES)가 " + ("있습니다." if has_auth else "없습니다. 로그인 브라우저에서 네이버에 로그인하세요.")
            return {"status": 200, "logged_in": has_auth, "nickname": None, "note": note}
        result = fetcher.fetch_page("https://arca.live/")
        logged_in = is_logged_in(result.html)
        nick = None
        if logged_in:
            import re

            match = re.search(r'class="[^"]*user-nick[^"]*"[^>]*>([^<]{1,40})<', result.html)
            nick = match.group(1).strip() if match else None
        return {"status": result.status, "logged_in": logged_in, "nickname": nick, "final_url": result.final_url}
    finally:
        fetcher.close()
