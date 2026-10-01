from __future__ import annotations
from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from .config import ADAPTER_VERSION, PROVIDER, CodexOptions
from .contract import compile_request, schema_issues, strict_json_loads, verify_prepared_spec
from .errors import CodexAdapterError, configuration_error
from .events import parse_run
from .runtime import CodexRuntime

# 같은 프로세스의 여러 Analyzer가 같은 로그 파일에 쓰는 경우 줄 섞임 방지.
_AUDIT_LOCK = threading.Lock()
ErrorMapper = Callable[[CodexAdapterError], Exception]


class CodexAnalyzer:
    """V9 Analyzer(key, spec, client=None)의 생성/호출 계약을 위한 어댑터.

    key는 사용/저장하지 않습니다. client 주입은 OpenAI SDK client가 아니라
    runtime= 테스트 의존성으로 분리합니다. 실운영에서 기존 client를 전달하면 거절합니다.
    """
    def __init__(self, key=None, spec: dict[str, Any] | None = None, client=None, *,
                 options: CodexOptions | None = None, runtime: CodexRuntime | None = None,
                 error_mapper: ErrorMapper | None = None):
        self.options = options or CodexOptions()
        if client is not None:
            raise configuration_error("OpenAI client는 Codex에 전달하지 않습니다. 테스트 주입은 runtime=을 사용하세요.")
        self._expected_version = verify_prepared_spec(spec, self.options) if spec is not None else None
        self._runtime = runtime or CodexRuntime(self.options)
        self._owns_runtime = runtime is None
        self._error_mapper = error_mapper
        self._closed = threading.Event()
        self._local = threading.local()

    @property
    def last_run_info(self) -> dict[str, Any] | None:
        """호출 스레드의 최근 기록. 전체 결과에는 adapter 메타데이터를 사용하세요."""
        return getattr(self._local, "last_run_info", None)

    def _record(self, record: dict[str, Any]) -> None:
        self._local.last_run_info = record
        if self.options.audit_log is None:
            return
        try:
            path = self.options.audit_log
            with _AUDIT_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        except OSError as exc:
            raise CodexAdapterError("CODEX_AUDIT_WRITE", "사용량 감사 로그 저장에 실패했습니다. 이미 실행한 모델을 다시 호출하지 마세요.",
                                    uncertain=True, diagnostics={"errno": exc.errno}) from exc

    def analyze(self, request: dict[str, Any]) -> dict[str, Any]:
        run_id = "codex-adapter-" + uuid.uuid4().hex
        started = time.monotonic()
        record: dict[str, Any] = {"adapter_run_id": run_id, "provider": PROVIDER,
            "adapter_version": ADAPTER_VERSION, "started_at": datetime.now(timezone.utc).isoformat(),
            "requested_model": self.options.model, "requested_reasoning_effort": self.options.reasoning_effort,
            "usage": None, "plus_5h_percent": None, "plus_weekly_percent": None}
        try:
            if self._closed.is_set():
                raise CodexAdapterError("CODEX_CLOSED", "이미 종료된 Analyzer입니다.")
            compiled = compile_request(request, self.options)
            record["request_sha256"] = compiled.request_hash
            if not self.options.acknowledge_cli_differences:
                raise configuration_error("CLI/API 차이를 확인한 뒤 acknowledge_cli_differences=True로 설정하세요.")
            report = self._runtime.preflight()
            if self._expected_version and report["cli_version"] != self._expected_version:
                raise configuration_error("spec 생성 후 Codex 버전이 달라졌습니다. 캐시 fingerprint를 다시 만드세요.")
            run = self._runtime.run(compiled, cancel=self._closed)
            parsed = parse_run(run)
            record.update({"cli_version": run.cli_version, "cli_thread_id": parsed.thread_id,
                           "event_count": parsed.event_count, "cli_elapsed_seconds": run.elapsed_seconds})
            usage = parsed.usage.response_dict() if parsed.usage else None
            record["usage"] = usage
            if parsed.usage is None:
                # cap을 비교할 관측치도 없다면 0으로 조작하거나 성공으로 진행하지 않습니다.
                raise CodexAdapterError("CODEX_USAGE_MISSING", "사용량이 보고되지 않아 출력 토큰 사후 제한을 검사할 수 없습니다.", uncertain=True)
            metadata = {"provider": PROVIDER, "adapter_version": ADAPTER_VERSION,
                "adapter_run_id": run_id, "cli_thread_id": parsed.thread_id, "cli_version": run.cli_version,
                "request_sha256": compiled.request_hash, "requested_model": self.options.model,
                "requested_reasoning_effort": self.options.reasoning_effort,
                "response_id_available": False, "usage_source": "codex_exec.turn.completed",
                "generation_token_cap_enforced": False, "requested_max_output_tokens": compiled.max_output_tokens,
                "api_store_false_equivalence_verified": False,
                "local_session_rollout": "ephemeral", "schema_name": compiled.schema_name,
                "host_validation_required": True}
            response: dict[str, Any] = {"id": None, "status": "completed", "incomplete_details": None,
                "output": [], "usage": usage, "adapter": metadata}
            if parsed.usage.output_tokens > compiled.max_output_tokens:
                # 생성 시점 상한이 아니라, 보고된 출력량에 대한 사후 거절입니다.
                response["status"] = "incomplete"
                response["incomplete_details"] = {"reason": "max_output_tokens"}
                metadata["adapter_limit_reason"] = "reported_output_exceeded_post_run_guard"
            else:
                if run.final_text is None or not run.final_text.strip():
                    raise CodexAdapterError("CODEX_NO_OUTPUT", "CLI가 최종 결과를 만들지 않았습니다.", uncertain=True)
                raw = run.final_text.strip()
                try:
                    candidate = strict_json_loads(raw)
                except ValueError as exc:
                    raise CodexAdapterError("CODEX_INVALID_JSON", "최종 응답이 올바른 JSON이 아닙니다. 코드블록 제거/재분석을 자동 수행하지 않습니다.", stop=False) from exc
                if not isinstance(candidate, dict):
                    raise CodexAdapterError("CODEX_INVALID_JSON", "최종 분석은 JSON object여야 합니다.", stop=False)
                if "refusal" in candidate and "refusal" not in compiled.schema.get("properties", {}):
                    raise CodexAdapterError("CODEX_REFUSAL", "분석 대신 거절 응답이 반환되었습니다.", stop=False)
                try:
                    issues = schema_issues(candidate, compiled.schema)
                except Exception as exc:
                    raise CodexAdapterError("CODEX_SCHEMA_CHECK", "로컬 JSON Schema 평가에 실패했습니다.",
                                            stop=False, diagnostics={"error_type": type(exc).__name__}) from exc
                metadata["local_schema_issues"] = issues
                # completed는 생성 완료일 뿐 분석 승인 상태가 아닙니다.
                # 원본 후보를 그대로 넘겨 V9의 validate_candidate/repair 경로를 유지합니다.
                response["output"] = [{"type": "message", "role": "assistant", "status": "completed",
                                        "content": [{"type": "output_text", "text": raw}]}]
            record["result_status"] = response["status"]
            record["local_schema_issue_count"] = len(metadata.get("local_schema_issues", []))
            record["elapsed_seconds"] = time.monotonic() - started
            self._record(record)
            return response
        except CodexAdapterError as exc:
            record.update({"result_status": "error", "error_code": exc.code,
                "stop": exc.stop, "uncertain": exc.uncertain, "elapsed_seconds": time.monotonic() - started})
            # 로그 자체 실패에 대한 무한 재시도를 피합니다.
            if exc.code != "CODEX_AUDIT_WRITE":
                try:
                    self._record(record)
                except CodexAdapterError:
                    exc.diagnostics["audit_write_failed"] = True
            else:
                self._local.last_run_info = record
            if self._error_mapper is not None:
                mapped = self._error_mapper(exc)
                if not isinstance(mapped, Exception):
                    raise configuration_error("error_mapper는 실제 예외 객체를 반환해야 합니다.") from exc
                raise mapped from exc
            raise

    def close(self) -> None:
        self._closed.set()
        if self._owns_runtime:
            self._runtime.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
