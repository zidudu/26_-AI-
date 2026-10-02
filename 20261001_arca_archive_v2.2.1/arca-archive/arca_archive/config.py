"""설정 로드/검증. config/settings.json 이 없으면 기본값으로 생성합니다."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from .common import AppError, atomic_write_json, read_json

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "settings.json"


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8766, ge=1, le=65535)


class HttpSettings(BaseModel):
    timeout_seconds: float = Field(30, ge=5, le=180)
    page_delay_seconds: float = Field(2.0, ge=0.5, le=60, description="페이지 요청 사이 최소 대기")
    page_delay_jitter_seconds: float = Field(1.0, ge=0, le=30)
    media_delay_seconds: float = Field(0.4, ge=0, le=30)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )
    max_consecutive_network_errors: int = Field(5, ge=1, le=50)


class BrowserSettings(BaseModel):
    channel: Literal["chrome", "msedge", "chromium"] = "chrome"
    headless: bool = True
    profile_dir: str = "data/browser_profile"
    navigation_timeout_seconds: float = Field(45, ge=10, le=180)
    challenge_wait_seconds: float = Field(25, ge=0, le=120, description="Cloudflare 확인 화면 자동 통과 대기")
    viewport_width: int = 1365
    viewport_height: int = 900


class MediaSettings(BaseModel):
    allowed_hosts: list[str] = Field(default_factory=lambda: [
        "ac.arca.live", "ac.namu.la", "ac2.namu.la", "ac-p1.namu.la", "ac-p2.namu.la", "ac-o.namu.la",
    ])
    max_file_mb: int = Field(300, ge=1, le=10000)
    backlog_interval_minutes: int = Field(15, ge=1, le=1440, description="기존 대기 전용 작업의 실행 간격")
    backlog_time_budget_minutes: int = Field(10, ge=1, le=240, description="기존 대기 전용 작업의 시간 예산")
    backlog_files_per_article: int = Field(8, ge=1, le=500, description="대기 전용 작업에서 한 글이 차지하는 파일 수")
    backlog_time_budget_by_channel: dict[str, Annotated[int, Field(ge=1, le=240)]] = Field(
        default_factory=dict, description="채널별 대기 작업 시간 예산(분); 없는 채널은 공통값 사용")
    backlog_files_per_article_by_channel: dict[str, Annotated[int, Field(ge=1, le=500)]] = Field(
        default_factory=dict, description="채널별 대기 작업의 글당 파일 상한; 없는 채널은 공통값 사용")
    max_download_seconds: int = Field(600, ge=10, le=7200, description="미디어 요청 한 번의 전체 시간 한도")
    request_original: bool = Field(True, description="가능하면 원본(type=orig) 요청. 원본은 CDN 캐시가 없어 파일당 수 초 걸릴 수 있음")
    include_emoticons: bool = Field(True, description="아카콘(이모티콘)도 미디어로 저장. 아카콘만 있는 글의 내용을 보존하려면 켜 둠")
    max_files_per_article_per_run: int = Field(80, ge=1, le=5000, description="한 실행에서 글 하나당 내려받는 최대 파일 수. 초과분은 다음 실행에서 URL을 갱신해 이어받음")
    run_time_budget_minutes: int = Field(40, ge=1, le=1440, description="한 실행의 미디어 다운로드 총 시간 예산")
    recheck_reserve_minutes: int = Field(10, ge=0, le=240, description="재확인 단계(밀린 미디어 처리)에 예산과 무관하게 보장하는 최소 시간")
    original_connect_timeout_seconds: float = Field(5, ge=1, le=60, description="원본 서버 연결 타임아웃. 연결 불가가 반복되면 원본 요청을 잠시 건너뜀")
    original_delay_seconds: float = Field(1.0, ge=0, le=30, description="원본 요청 사이 최소 간격(서버 부담·차단 위험 완화)")


class RetrySettings(BaseModel):
    max_attempts: int = Field(4, ge=1, le=20)
    base_delay_minutes: int = Field(10, ge=1, le=1440)
    max_delay_minutes: int = Field(360, ge=1, le=10080)


class CrawlSettings(BaseModel):
    default_interval_minutes: int = Field(60, ge=1, le=10080)
    default_initial_pages: int = Field(2, ge=1, le=200)
    default_max_pages_per_run: int = Field(20, ge=1, le=500)
    max_articles_per_run: int = Field(200, ge=1, le=5000)
    recheck_days: int = Field(3, ge=0, le=365)
    recheck_interval_hours: int = Field(24, ge=1, le=24 * 30)
    max_rechecks_per_run: int = Field(120, ge=0, le=1000, description="한 실행에서 다시 여는 글 수(미수신 미디어·원본 승격·수정 감지 포함)")
    collect_comments: bool = True
    max_comment_pages_per_article: int = Field(10, ge=1, le=100)
    keep_raw_html: bool = True
    keep_events_days: int = Field(30, ge=1, le=3650, description="실행 이벤트 로그 보존 일수. 실행 요약(runs)은 계속 보존")


class Settings(BaseModel):
    data_dir: str = "data"
    server: ServerSettings = ServerSettings()
    http: HttpSettings = HttpSettings()
    browser: BrowserSettings = BrowserSettings()
    media: MediaSettings = MediaSettings()
    retry: RetrySettings = RetrySettings()
    crawl: CrawlSettings = CrawlSettings()
    scheduler_enabled: bool = True
    seed_channels: list[str] = Field(default_factory=lambda: ["ailove"], description="첫 실행 시 등록할 채널 slug")

    _config_path: Path = PrivateAttr(default=DEFAULT_CONFIG_PATH)

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def root(self) -> Path:
        return self._config_path.resolve().parent.parent

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def data_path(self) -> Path:
        return self.resolve(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / "arca.sqlite3"

    @property
    def media_path(self) -> Path:
        return self.data_path / "media"

    @property
    def raw_path(self) -> Path:
        return self.data_path / "raw"

    @property
    def log_path(self) -> Path:
        return self.data_path / "logs"

    @property
    def profile_path(self) -> Path:
        return self.resolve(self.browser.profile_dir)

    @property
    def lock_path(self) -> Path:
        return self.data_path / "crawler.lock"


def load_settings(path: Path | None = None, create: bool = True) -> Settings:
    path = Path(path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        if not create:
            raise AppError("CONFIG_MISSING", f"설정 파일이 없습니다: {path}", stop=True, retryable=False)
        settings = Settings()
        settings._config_path = path
        atomic_write_json(path, settings.model_dump())
        return settings
    try:
        raw = read_json(path)
        settings = Settings.model_validate(raw)
    except (OSError, ValueError, ValidationError) as exc:
        raise AppError("CONFIG_ERROR", f"설정 파일을 읽지 못했습니다: {path} ({type(exc).__name__})",
                       stop=True, retryable=False) from None
    settings._config_path = path
    return settings


def save_settings(settings: Settings) -> None:
    atomic_write_json(settings._config_path, settings.model_dump())
