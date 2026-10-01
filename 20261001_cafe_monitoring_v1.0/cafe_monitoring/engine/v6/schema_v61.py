"""6.1 규격: 추출값과 근거를 같은 객체로 요청하고 항목별로 검증합니다."""
from __future__ import annotations

import re
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .common import V6Error
from .legacy_schema import LABELS

SCHEMA_VERSION = "6.1"
VALIDATOR_VERSION = "2"
Quote = Annotated[str, Field(min_length=1, max_length=400)]
Text = Annotated[str, Field(min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(StrictModel):
    value: Text
    quote: Quote


class Vehicle(StrictModel):
    model: Fact | None
    model_year: Fact | None
    mileage: Fact | None


class Cause(StrictModel):
    description: Text
    basis: Literal["workshop_report", "author_guess"]
    quote: Quote


class Action(StrictModel):
    description: Text
    status: Literal["completed", "planned", "recommended", "unavailable", "unclear"]
    quote: Quote


class Outcome(StrictModel):
    value: Literal["resolved", "unresolved", "temporary_improvement", "not_stated", "unclear"]
    quote: Quote | None


class Analysis(StrictModel):
    document_type: Literal["issue_experience", "repair_review", "question_information", "promotion", "other", "unclear"]
    quality_issue: Literal["yes", "no", "unclear"]
    firsthand_experience: Literal["yes", "no", "unclear"]
    classification_evidence: Annotated[list[Quote], Field(min_length=1, max_length=5)]
    vehicle: Vehicle
    symptoms: Annotated[list[Fact], Field(max_length=20)]
    parts_or_functions: Annotated[list[Fact], Field(max_length=20)]
    situation: Fact | None
    diagnostic_findings: Annotated[list[Fact], Field(max_length=20)]
    reported_cause: Cause | None
    actions: Annotated[list[Action], Field(max_length=20)]
    outcome: Outcome
    summary: Annotated[str, Field(min_length=1, max_length=1600)]
    focus_relevance: Literal["related", "unrelated", "unclear"]
    focus_reason: Text
    focus_evidence: Annotated[list[Quote], Field(min_length=1, max_length=5)]
    needs_review: bool
    review_reasons: Annotated[list[Text], Field(max_length=20)]


def normalize(text):
    return " ".join(text.split())


def _semantic_guards(data):
    """명백한 일부 모순만 거절합니다. 일반적인 의미 검증을 대체하지 않습니다."""
    for i, action in enumerate(data["actions"]):
        quote = re.sub(r"\s+", "", action["quote"])
        # 하나의 근거에 불가/권유와 실제 완료가 함께 있으면 문맥 판단을 사람에게 남깁니다.
        unavailable = re.search(r"예약(?:이|은|도)?(?:풀|full|마감|불가|불가능|안[돼되됨]|못)", quote, re.I)
        booked = re.search(r"예약(?:을)?(?:했|완료|잡았|성공)", quote)
        future_plan = re.search(r"예약.*(?:예정|계획|하려)|(?:내일|다음주|다음달).*예약", quote)
        contradicted = action["status"] == "completed" or (action["status"] == "planned" and not future_plan)
        if "예약" in action["description"] and unavailable and not booked and contradicted:
            raise V6Error("INVALID_SEMANTICS", f"actions[{i}].status: 예약 불가 근거를 예약 완료·확정 계획으로 표시했습니다.")
        recommendation = re.search(r"가라(?:고|함)|권유|권장|추천|신고하라|방문하라", quote)
        completion = re.search(r"(?:방문|교체|수리|신고|접수|예약|입고)(?:을|를)?(?:했|하였|완료)|다녀왔|갔(?:어요|습니다|다)", quote)
        if recommendation and not completion and re.search(r"방문|신고|예약", action["description"]) and action["status"] == "completed":
            raise V6Error("INVALID_SEMANTICS", f"actions[{i}].status: 권유만 있는 근거를 조치 완료로 표시했습니다.")
    cause = data["reported_cause"]
    if cause:
        quote = normalize(cause["quote"])
        # 경고 발생 사실 자체는 원인 진단이 아닙니다. 명시적 인과 설명이 있으면 여기서는 거절하지 않습니다.
        warning = re.search(r"경고등|점검\s*(?:뜸|뜬|떠|뜨|떴)", quote)
        description_is_warning = re.search(r"경고|점검\s*(?:뜸|뜬|떠|뜨|떴)", cause["description"])
        causal_detail = re.search(r"원인|때문|인해|불량|고장|결함|단선|누유|접촉|문제|진단|막힘|실화|으로", quote)
        if warning and description_is_warning and not causal_detail:
            raise V6Error("INVALID_SEMANTICS", "reported_cause.quote: 경고 표시만으로 원인을 지정했습니다. 증상과 점검 결과를 구분하세요.")


def validate_analysis(value, article, schema_version=SCHEMA_VERSION):
    if schema_version == "6.0":
        from .legacy_schema import validate_analysis as legacy_validate
        return legacy_validate(value, article)
    if schema_version != SCHEMA_VERSION:
        raise V6Error("PLAN_CHANGED", "지원하지 않는 분석 규격입니다.")
    try:
        result = Analysis.model_validate(value)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False, include_context=False)
        paths = [".".join(map(str, e["loc"])) for e in errors]
        code = "INVALID_EVIDENCE" if any("quote" in p or "evidence" in p for p in paths) else "INVALID_ANALYSIS"
        raise V6Error(code, "분석 규격 확인 필요: " + ", ".join(paths[:8])) from None
    except (ValueError, TypeError):
        raise V6Error("INVALID_ANALYSIS", "AI 응답을 분석 객체로 읽지 못했습니다.") from None
    data = result.model_dump()
    corpus = normalize(article["title"] + "\n" + article["body"])

    def check_quote(quote, path):
        text = normalize(quote or "")
        if not text:
            raise V6Error("INVALID_EVIDENCE", f"{path}: 원문 근거가 비어 있습니다.")
        if text not in corpus:
            raise V6Error("INVALID_EVIDENCE", f"{path}: 근거 구절을 원문에서 찾지 못했습니다.")

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, item in node.items():
                child = f"{path}.{key}" if path else key
                if key == "quote":
                    if item is not None:
                        check_quote(item, child)
                elif key in ("classification_evidence", "focus_evidence"):
                    for i, quote in enumerate(item):
                        check_quote(quote, f"{child}[{i}]")
                else:
                    walk(item, child)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")
        elif isinstance(node, str) and not node.strip():
            raise V6Error("INVALID_ANALYSIS", f"{path}: 공백만 있는 값은 허용하지 않습니다.")

    walk(data)
    outcome = data["outcome"]
    if outcome["value"] != "not_stated":
        check_quote(outcome["quote"], "outcome.quote")
    elif outcome["quote"] is not None:
        raise V6Error("INVALID_ANALYSIS", "outcome: 언급 없음이면 quote=null로 지정하세요.")
    _semantic_guards(data)
    reasons = list(data["review_reasons"])
    if len(article["body"].strip()) < 80:
        reasons.append("본문이 짧아 맥락을 충분히 확인하기 어렵습니다.")
    if any(data[k] == "unclear" for k in ("document_type", "quality_issue", "firsthand_experience", "focus_relevance")) or outcome["value"] == "unclear":
        reasons.append("AI가 판단을 보류한 항목이 있습니다.")
    if data["reported_cause"] and data["reported_cause"]["basis"] == "author_guess":
        reasons.append("고장 원인은 작성자의 추정이며 확인된 사실이 아닙니다.")
    if any(a["status"] == "unclear" for a in data["actions"]):
        reasons.append("실제로 수행된 조치인지 불명확한 항목이 있습니다.")
    if data["needs_review"] and not reasons:
        reasons.append("AI가 원문 검토를 요청했습니다.")
    data["review_reasons"] = list(dict.fromkeys(reasons))[:20]
    data["needs_review"] = bool(data["needs_review"] or reasons)
    return data


def validate_spec(spec):
    """재개 시 당시 스키마를 그대로 쓰되, 지원 규격/해시가 맞는지 확인합니다."""
    from .common import digest
    from .legacy_schema import Analysis as LegacyAnalysis
    versions = {"6.0": ("1", LegacyAnalysis), SCHEMA_VERSION: (VALIDATOR_VERSION, Analysis)}
    version, model = versions.get(spec.get("schema_version"), (None, None))
    if (model is None or spec.get("validator_version") != version or
            spec.get("schema") != model.model_json_schema() or
            spec.get("fingerprint") != digest({k: v for k, v in spec.items() if k not in ("fingerprint", "timeout_seconds")})):
        raise V6Error("PLAN_CHANGED", "저장된 실행의 분석 규격이 지원 버전과 다르거나 손상되었습니다.")
