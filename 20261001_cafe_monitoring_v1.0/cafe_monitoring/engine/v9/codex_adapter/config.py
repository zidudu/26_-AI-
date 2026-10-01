from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math

from .errors import configuration_error

ADAPTER_VERSION = "2.0.0"
PROVIDER = "codex_cli_chatgpt"


@dataclass(frozen=True)
class CodexOptions:
    # 사용자 PC에서 실행 확인된 값입니다. 모델을 몰래 대체하지 않습니다.
    model: str = "gpt-6-astra"
    reasoning_effort: str = "medium"
    command: str = "codex"
    # 필요할 때만 native exe 또는 (node.exe, codex.js)를 명시합니다.
    command_prefix: tuple[str, ...] | None = None
    timeout_seconds: float = 120.0
    preflight_timeout_seconds: float = 30.0
    max_parallel: int = 1  # V9의 호출 동시성 3도 지원. 초기 시험은 1.
    max_request_bytes: int = 2_000_000
    max_final_bytes: int = 2_000_000
    max_event_bytes: int = 8_000_000
    temp_root: Path | None = None
    codex_home: Path | None = None
    credentials_store: str = "auto"
    audit_log: Path | None = None
    # CLI의 token cap / store / role mapping은 Responses API와 같지 않습니다.
    # 실제 실행하려면 문서를 읽은 후 명시적으로 승인합니다.
    acknowledge_cli_differences: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model or any(c.isspace() for c in self.model):
            raise configuration_error("model은 공백 없는 명시적 모델 ID여야 합니다.")
        if self.reasoning_effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
            raise configuration_error("지원하지 않는 reasoning_effort입니다.")
        for name in ("timeout_seconds", "preflight_timeout_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise configuration_error(f"{name}은 유한한 양수여야 합니다.")
        if type(self.max_parallel) is not int or not 1 <= self.max_parallel <= 3:
            raise configuration_error("max_parallel은 1~3이어야 합니다.")
        for name in ("max_request_bytes", "max_final_bytes", "max_event_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise configuration_error(f"{name}은 양의 정수여야 합니다.")
        if self.credentials_store not in {"auto", "file", "keyring"}:
            raise configuration_error("credentials_store는 auto/file/keyring 중 하나입니다.")
        if self.command_prefix is not None:
            if not isinstance(self.command_prefix, tuple) or not self.command_prefix or not all(isinstance(v, str) and v for v in self.command_prefix):
                raise configuration_error("command_prefix는 비어 있지 않은 문자열 tuple이어야 합니다.")
        for name in ("temp_root", "codex_home", "audit_log"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value).expanduser().absolute())
