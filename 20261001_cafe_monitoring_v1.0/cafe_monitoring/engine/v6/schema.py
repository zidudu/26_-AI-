"""Monitoring drafts. Shape failures are technical; uncertain meaning is reviewed."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .common import V6Error, digest
from .evidence import EVIDENCE_VERSION
from .legacy_schema import LABELS
from .schema_v62 import semantic_issues

SCHEMA_VERSION = "6.3"
VALIDATOR_VERSION = "4"
Text = Annotated[str, Field(max_length=1600)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Vehicle(StrictModel):
    model: Text | None
    model_year: Text | None
    mileage: Text | None


class Action(StrictModel):
    description: Text
    status: Literal["completed", "planned", "recommended", "unavailable", "unclear"]


class Analysis(StrictModel):
    document_type: Literal["issue_experience", "repair_review", "question_information", "promotion", "other", "unclear"]
    vehicle: Vehicle
    summary: Text
    symptoms: Annotated[list[Text], Field(max_length=20)]
    parts_or_functions: Annotated[list[Text], Field(max_length=20)]
    actions: Annotated[list[Action], Field(max_length=20)]
    focus_relevance: Literal["related", "unrelated", "unclear"]
    evidence_ids: Annotated[list[str], Field(max_length=40)]
    needs_review: bool
    review_reasons: Annotated[list[Text], Field(max_length=20)]


def validate_analysis(value, article, schema_version=SCHEMA_VERSION):
    if schema_version in ("6.0", "6.1", "6.2"):
        from .schema_v62 import validate_analysis as previous
        return previous(value, article, schema_version)
    if schema_version != SCHEMA_VERSION:
        raise V6Error("PLAN_CHANGED", "지원하지 않는 분석 규격입니다.")
    try:
        return Analysis.model_validate(value).model_dump()
    except ValidationError as exc:
        paths = [".".join(map(str, e["loc"])) for e in exc.errors(include_input=False, include_url=False, include_context=False)]
        raise V6Error("INVALID_ANALYSIS", "응답을 초안 항목으로 읽을 수 없습니다: " + ", ".join(paths[:8])) from None


def validate_spec(spec):
    if spec.get("schema_version") != SCHEMA_VERSION:
        from .schema_v62 import validate_spec as previous
        return previous(spec)
    if (spec.get("validator_version") != VALIDATOR_VERSION or spec.get("evidence_version") != EVIDENCE_VERSION
            or spec.get("schema") != Analysis.model_json_schema()
            or spec.get("fingerprint") != digest({k: v for k, v in spec.items() if k not in ("fingerprint", "timeout_seconds")})):
        raise V6Error("PLAN_CHANGED", "저장된 실행의 초안 규격이 손상되었거나 변경됐습니다.")
