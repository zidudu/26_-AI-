"""V9의 두 analyzer_factory에 동일 인스턴스를 주입합니다."""
from __future__ import annotations
from typing import Any
from .analyzer import CodexAnalyzer, ErrorMapper
from .config import CodexOptions
from .runtime import CodexRuntime
from .errors import CodexAdapterError, configuration_error


class CodexAnalyzerFactory:
    def __init__(self, options: CodexOptions, *, error_mapper: ErrorMapper | None = None):
        self.options = options
        self.error_mapper = error_mapper
        self.runtime = CodexRuntime(options)

    def preflight(self) -> dict[str, Any]:
        return self.runtime.preflight()

    def __call__(self, key, spec, client=None) -> CodexAnalyzer:
        try:
            return CodexAnalyzer(key, spec, client, options=self.options,
                                 runtime=self.runtime, error_mapper=self.error_mapper)
        except CodexAdapterError as exc:
            if self.error_mapper is not None:
                mapped = self.error_mapper(exc)
                if not isinstance(mapped, Exception):
                    raise configuration_error("error_mapper는 실제 예외 객체를 반환해야 합니다.") from exc
                raise mapped from exc
            raise

    def close(self) -> None:
        self.runtime.close()
