"""V6.0/검증 1 실행의 재개 전용. 당시 규격을 보존합니다."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, ValidationError
from .common import V6Error

SCHEMA_VERSION = "6.0"
VALIDATOR_VERSION = "1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Vehicle(StrictModel):
    model: str | None
    model_year: str | None
    mileage: str | None


class Cause(StrictModel):
    description: str | None
    basis: Literal["workshop_report", "author_guess", "not_stated"]


class Evidence(StrictModel):
    field: Literal["classification", "vehicle", "symptoms", "parts_or_functions", "situation",
                   "reported_cause", "actions", "outcome", "focus_relevance"]
    quote: str


class Analysis(StrictModel):
    document_type: Literal["issue_experience", "repair_review", "question_information", "promotion", "other", "unclear"]
    quality_issue: Literal["yes", "no", "unclear"]
    firsthand_experience: Literal["yes", "no", "unclear"]
    vehicle: Vehicle
    symptoms: list[str]
    parts_or_functions: list[str]
    situation: str | None
    reported_cause: Cause
    actions: list[str]
    outcome: Literal["resolved", "unresolved", "temporary_improvement", "not_stated", "unclear"]
    summary: str
    focus_relevance: Literal["related", "unrelated", "unclear"]
    focus_reason: str
    evidence: list[Evidence]
    needs_review: bool
    review_reasons: list[str]


LABELS = {"issue_experience": "문제 경험담", "repair_review": "수리·해결 후기",
          "question_information": "질문·정보 공유", "promotion": "광고·홍보",
          "other": "기타", "unclear": "판단 보류"}


def validate_analysis(value, article):
    try:
        result = Analysis.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise V6Error("INVALID_ANALYSIS", "AI 응답의 항목 또는 자료형이 분석 규격과 다릅니다.") from None
    if not result.summary.strip() or len(result.summary) > 1600 or not result.focus_reason.strip():
        raise V6Error("INVALID_ANALYSIS", "요약 또는 관련성 설명의 길이가 올바르지 않습니다.")
    if not 1 <= len(result.evidence) <= 30:
        raise V6Error("INVALID_EVIDENCE", "판단 근거가 없거나 너무 많습니다.")
    corpus = " ".join((article["title"] + "\n" + article["body"]).split())
    fields = set()
    for item in result.evidence:
        quote = " ".join(item.quote.split())
        if not quote or len(quote) > 400 or quote not in corpus:
            raise V6Error("INVALID_EVIDENCE", f"evidence.{item.field}: 근거 구절이 비어 있거나 400자를 넘거나 원문에 없습니다.")
        fields.add(item.field)
    required = {"classification"}
    for field in ("symptoms", "parts_or_functions", "actions"):
        entries = getattr(result, field)
        if len(entries) > 20 or any(not x.strip() or len(x) > 500 for x in entries):
            raise V6Error("INVALID_ANALYSIS", "추출 항목의 개수 또는 길이가 올바르지 않습니다.")
        if entries:
            required.add(field)
    if any(x is not None for x in result.vehicle.model_dump().values()):
        required.add("vehicle")
    if result.situation:
        required.add("situation")
    if result.reported_cause.description:
        required.add("reported_cause")
    if (result.reported_cause.description is None) != (result.reported_cause.basis == "not_stated"):
        raise V6Error("INVALID_ANALYSIS", "원인 설명과 원인 근거의 표시가 일치하지 않습니다.")
    if result.outcome in ("resolved", "unresolved", "temporary_improvement"):
        required.add("outcome")
    if result.focus_relevance == "related":
        required.add("focus_relevance")
    if not required.issubset(fields):
        raise V6Error("INVALID_EVIDENCE", "원문 근거가 빠진 항목: " + ", ".join(sorted(required - fields)))
    data = result.model_dump()
    reasons = [r.strip() for r in data["review_reasons"] if r.strip()]
    if len(article["body"].strip()) < 80:
        reasons.append("본문이 짧아 맥락을 충분히 확인하기 어렵습니다.")
    if any(data[k] == "unclear" for k in ("document_type", "quality_issue", "firsthand_experience", "focus_relevance", "outcome")):
        reasons.append("AI가 판단을 보류한 항목이 있습니다.")
    if data["reported_cause"]["basis"] == "author_guess":
        reasons.append("고장 원인은 작성자의 추정이며 확인된 사실이 아닙니다.")
    if data["needs_review"] and not reasons:
        reasons.append("AI가 원문 검토를 요청했습니다.")
    data["review_reasons"] = list(dict.fromkeys(reasons))
    data["needs_review"] = bool(data["needs_review"] or reasons)
    return data
