"""어댑터 오류. V9의 실제 APIError 생성자는 error_mapper로 연결합니다."""
from __future__ import annotations
from typing import Any


class CodexAdapterError(RuntimeError):
    def __init__(self, code: str, message: str, *, stop: bool = True,
                 uncertain: bool = False, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.stop = stop
        self.uncertain = uncertain
        self.diagnostics = dict(diagnostics or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "stop": self.stop,
                "uncertain": self.uncertain, "diagnostics": self.diagnostics}


def configuration_error(message: str) -> CodexAdapterError:
    return CodexAdapterError("CODEX_CONFIGURATION", message)
