"""--json 이벤트에서 실제 보고된 사용량만 추출합니다."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .contract import strict_json_loads
from .errors import CodexAdapterError
from .runtime import RunOutput


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None

    def response_dict(self) -> dict[str, Any]:
        # cached는 input의 일부, reasoning은 output의 일부입니다. 재합산하지 않습니다.
        out: dict[str, Any] = {"input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens}
        if self.cached_input_tokens is not None:
            out["input_tokens_details"] = {"cached_tokens": self.cached_input_tokens}
        if self.reasoning_output_tokens is not None:
            out["output_tokens_details"] = {"reasoning_tokens": self.reasoning_output_tokens}
        return out


@dataclass(frozen=True)
class ParsedRun:
    thread_id: str | None
    usage: Usage | None
    event_count: int


def classify_execution_failure(text: str, *, returncode: int | None = None) -> CodexAdapterError:
    # 원문/인증 문자열을 오류 로그에 그대로 복사하지 않습니다.
    low = text.lower()
    diagnostics = {"returncode": returncode}
    if any(s in low for s in ("usage limit", "rate limit", "rate_limit", "quota exceeded", "insufficient_quota")):
        return CodexAdapterError("CODEX_RATE_LIMIT", "Codex 사용 한도 또는 호출 제한에 도달했습니다. 자동 재시도하지 않습니다.", diagnostics=diagnostics)
    if any(s in low for s in ("unauthorized", "not logged in", "authentication", "token expired", "login required", "401")):
        return CodexAdapterError("CODEX_AUTH_FAILED", "Codex 인증을 확인하세요. 자동 로그아웃/재로그인하지 않습니다.", diagnostics=diagnostics)
    if any(s in low for s in ("model is not supported", "unsupported model", "model_not_found", "not available for", "not supported when")):
        return CodexAdapterError("CODEX_MODEL_UNAVAILABLE", "지정 모델을 현재 계정에서 사용할 수 없습니다. 다른 모델로 자동 전환하지 않습니다.", diagnostics=diagnostics)
    if any(s in low for s in ("unknown field", "unknown feature", "unexpected argument", "unrecognized", "invalid configuration")):
        return CodexAdapterError("CODEX_CLI_OPTIONS", "현재 Codex가 어댑터 설정/옵션을 지원하지 않습니다. 보호 옵션을 자동 제거하지 않습니다.", diagnostics=diagnostics)
    return CodexAdapterError("CODEX_EXECUTION_FAILED", "Codex 실행이 정상 완료되지 않았습니다. 처리/사용량 확정이 어려우므로 자동 재시도하지 않습니다.", uncertain=True, diagnostics=diagnostics)


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise CodexAdapterError("CODEX_USAGE_INVALID", f"실제 CLI 사용량의 {name} 값이 올바르지 않습니다.", uncertain=True)
    return value


def parse_run(run: RunOutput) -> ParsedRun:
    events: list[dict[str, Any]] = []
    for line in run.events_text.splitlines():
        if not line.strip():
            continue
        try:
            value = strict_json_loads(line.lstrip("\ufeff"))
        except (TypeError, ValueError) as exc:
            if run.returncode != 0:
                raise classify_execution_failure(run.stderr_text, returncode=run.returncode) from exc
            raise CodexAdapterError("CODEX_EVENTS_INVALID", "CLI JSONL 이벤트를 해석할 수 없습니다.", uncertain=True) from exc
        if not isinstance(value, dict):
            raise CodexAdapterError("CODEX_EVENTS_INVALID", "CLI 이벤트가 JSON object가 아닙니다.", uncertain=True)
        events.append(value)
    if run.returncode != 0:
        # 에러 텍스트는 분류에만 쓰고 diagnostics에는 저장하지 않습니다.
        raise classify_execution_failure(run.stderr_text + "\n" + run.events_text, returncode=run.returncode)
    completed = []
    thread_id = None
    allowed_items = {"agent_message", "reasoning", "plan", "todo_list"}
    for event in events:
        kind = event.get("type")
        if kind in {"error", "turn.failed"}:
            raise classify_execution_failure(str(event.get("error", event)), returncode=run.returncode)
        if kind == "thread.started":
            tid = event.get("thread_id")
            if isinstance(tid, str):
                if thread_id is not None and thread_id != tid:
                    raise CodexAdapterError("CODEX_EVENTS_INVALID", "한 실행에서 여러 thread_id가 보고되었습니다.", uncertain=True)
                thread_id = tid
        if kind == "turn.completed":
            completed.append(event)
        if isinstance(kind, str) and kind.startswith("item."):
            item = event.get("item", {})
            itype = item.get("type") if isinstance(item, dict) else None
            if itype == "refusal" or (isinstance(item, dict) and item.get("refusal")):
                raise CodexAdapterError("CODEX_REFUSAL", "Codex가 요청을 거절했습니다.", stop=False)
            if itype and itype not in allowed_items:
                raise CodexAdapterError("CODEX_UNEXPECTED_TOOL", "텍스트 분석 외의 도구/동작이 보고되어 결과를 채택하지 않습니다.",
                                        uncertain=True, diagnostics={"item_type": itype})
    if len(completed) != 1:
        raise CodexAdapterError("CODEX_NOT_COMPLETED", "정확히 하나의 turn.completed를 확인해야 합니다.",
                                uncertain=True, diagnostics={"completed_events": len(completed)})
    raw = completed[0].get("usage")
    usage = None
    if raw is not None:
        if not isinstance(raw, dict):
            raise CodexAdapterError("CODEX_USAGE_INVALID", "CLI usage가 object가 아닙니다.", uncertain=True)
        inp = _count(raw.get("input_tokens"), "input_tokens")
        out = _count(raw.get("output_tokens"), "output_tokens")
        cached = _count(raw["cached_input_tokens"], "cached_input_tokens") if "cached_input_tokens" in raw else None
        reasoning = _count(raw["reasoning_output_tokens"], "reasoning_output_tokens") if "reasoning_output_tokens" in raw else None
        if (cached is not None and cached > inp) or (reasoning is not None and reasoning > out):
            raise CodexAdapterError("CODEX_USAGE_INVALID", "입력/출력과 상세 토큰 수가 서로 맞지 않습니다.", uncertain=True)
        usage = Usage(inp, out, cached, reasoning)
    return ParsedRun(thread_id, usage, len(events))
