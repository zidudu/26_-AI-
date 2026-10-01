"""6.2: source-ID evidence, separate onset/course, and bounded semantic guards."""
from __future__ import annotations

import re
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .common import V6Error, digest
from .evidence import EVIDENCE_VERSION, evidence_text, resolve_evidence
from .legacy_schema import LABELS

SCHEMA_VERSION = "6.2"
VALIDATOR_VERSION = "3"
Text = Annotated[str, Field(min_length=1, max_length=500)]
EvidenceID = Annotated[str, Field(pattern=r"^[TB][0-9]{4,}$")]
Refs = Annotated[list[EvidenceID], Field(min_length=1, max_length=16)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Fact(StrictModel):
    value: Text
    evidence_ids: Refs


class Vehicle(StrictModel):
    model: Fact | None
    model_year: Fact | None
    mileage: Fact | None
    delivery_date: Fact | None
    purchase_date: Fact | None


class Situation(StrictModel):
    onset_conditions: Fact | None
    course: Annotated[list[Fact], Field(max_length=20)]


class Cause(StrictModel):
    description: Text
    basis: Literal["workshop_report", "author_guess"]
    evidence_ids: Refs


class Action(StrictModel):
    description: Text
    status: Literal["completed", "planned", "recommended", "unavailable", "unclear"]
    evidence_ids: Refs


class Outcome(StrictModel):
    value: Literal["resolved", "unresolved", "temporary_improvement", "not_stated", "unclear"]
    evidence_ids: Annotated[list[EvidenceID], Field(max_length=16)]


class Analysis(StrictModel):
    document_type: Literal["issue_experience", "repair_review", "question_information", "promotion", "other", "unclear"]
    quality_issue: Literal["yes", "no", "unclear"]
    firsthand_experience: Literal["yes", "no", "unclear"]
    classification_evidence_ids: Refs
    vehicle: Vehicle
    symptoms: Annotated[list[Fact], Field(max_length=20)]
    parts_or_functions: Annotated[list[Fact], Field(max_length=20)]
    situation: Situation
    diagnostic_findings: Annotated[list[Fact], Field(max_length=20)]
    reported_cause: Cause | None
    actions: Annotated[list[Action], Field(max_length=20)]
    outcome: Outcome
    summary: Annotated[str, Field(min_length=1, max_length=1600)]
    focus_relevance: Literal["related", "unrelated", "unclear"]
    focus_reason: Text
    focus_evidence_ids: Refs
    needs_review: bool
    review_reasons: Annotated[list[Text], Field(max_length=20)]


def semantic_issues(data, article, text_of):
    """Flag known contradictions, not a general proof that all meanings are correct.

    Also accepts 6.1 data for offline audits. No values are silently corrected.
    """
    issues = []
    def issue(path, message):
        issues.append({"field": path, "message": message})
    year = data["vehicle"]["model_year"]
    if year:
        q = text_of(year)
        # Dates alone are insufficient. Require a model-year expression and one year only.
        if (not re.fullmatch(r"(?:19|20)\d{2}(?:년(?:식|형)?)?|\d{2}년(?:식|형)", year["value"].strip())
                or not re.search(r"\d{2,4}\s*(?:년\s*(?:식|형)|MY\b)|(?:연식|모델연도|model\s*year|MY)\s*[:：]?\s*\d{2,4}", q, re.I)):
            issue("vehicle.model_year", "출고·구매 시점이나 모호한 날짜를 모델 연식으로 확정할 수 없습니다.")
    for i, finding in enumerate(data["diagnostic_findings"]):
        q = text_of(finding)
        warning = re.search(r"경고|점검\s*(?:뜸|떠|뜨|떴)|시스템.*오류", q)
        test_result = re.search(r"스캔|스캐너|진단기|검사\s*결과|점검\s*결과|검사에서|점검에서|측정|테스트|진단\s*(?:결과|받|했)|고장\s*코드|DTC|정비사.*(?:확인|진단)|정비소.*(?:확인|진단)", q, re.I)
        if warning and not test_result:
            issue(f"diagnostic_findings[{i}]", "경고 표시·점검 후 발생 경과만으로 검사 결과를 지정했습니다.")
    # Reuse the preceding version's narrow action/cause guards on resolved text.
    from .schema_v61 import _semantic_guards
    proxy = {"actions": [{**a, "quote": text_of(a)} for a in data["actions"]],
             "reported_cause": ({**data["reported_cause"], "quote": text_of(data["reported_cause"])}
                                if data["reported_cause"] else None)}
    try:
        _semantic_guards(proxy)
    except V6Error as exc:
        issue("actions/reported_cause", str(exc))
    onset_pattern = r"주행\s*중|운전\s*중|달리(?:던|는)\s*중"
    corpus = article["title"] + "\n" + article["body"]
    situation = data.get("situation")
    onset = situation.get("onset_conditions") if isinstance(situation, dict) else None
    course = situation.get("course", []) if isinstance(situation, dict) else []
    # Legacy situation is a single fact.
    facts = [("situation.onset_conditions", onset)] + [(f"situation.course[{i}]", f) for i, f in enumerate(course)]
    if situation and "value" in situation:
        facts.append(("situation", situation))
    for path, fact in facts:
        if fact and re.search(onset_pattern, fact["value"]) and not re.search(onset_pattern, text_of(fact)):
            issue(path, "주행 이후의 경과만으로 주행 중 발생했다고 단정했습니다. 발생 조건 근거를 확인하세요.")
    if re.search(onset_pattern, data["summary"]) and not re.search(onset_pattern, corpus):
        issue("summary", "원문에 없는 '주행 중 발생'을 요약에 추가했습니다.")
    return issues


def validate_analysis(value, article, schema_version=SCHEMA_VERSION):
    if schema_version == "6.0":
        from .legacy_schema import validate_analysis as validate_old
        return validate_old(value, article)
    if schema_version == "6.1":
        from .schema_v61 import validate_analysis as validate_old
        return validate_old(value, article)
    if schema_version != SCHEMA_VERSION:
        raise V6Error("PLAN_CHANGED", "지원하지 않는 분석 규격입니다.")
    try:
        data = Analysis.model_validate(value).model_dump()
    except ValidationError as exc:
        paths = [".".join(map(str, e["loc"])) for e in exc.errors(include_input=False, include_url=False, include_context=False)]
        code = "INVALID_EVIDENCE" if any("evidence" in p for p in paths) else "INVALID_ANALYSIS"
        raise V6Error(code, "분석 규격 확인 필요: " + ", ".join(paths[:8])) from None
    def check_blank(node):
        if isinstance(node, dict):
            for v in node.values(): check_blank(v)
        elif isinstance(node, list):
            for v in node: check_blank(v)
        elif isinstance(node, str) and not node.strip():
            raise V6Error("INVALID_ANALYSIS", "공백만 있는 분석 값은 허용하지 않습니다.")
    check_blank(data)
    resolved = resolve_evidence(data, article)
    outcome = data["outcome"]
    if (outcome["value"] == "not_stated") != (len(outcome["evidence_ids"]) == 0):
        raise V6Error("INVALID_EVIDENCE", "outcome.evidence_ids: 언급 없음은 [], 나머지 상태에는 근거 번호가 필요합니다.")
    issues = semantic_issues(data, article, lambda f: evidence_text(f, resolved["units"]))
    if issues:
        raise V6Error("INVALID_SEMANTICS", "; ".join(f"{i['field']}: {i['message']}" for i in issues[:5]))
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
    from .legacy_schema import Analysis as Analysis60
    from .schema_v61 import Analysis as Analysis61
    versions = {"6.0": ("1", Analysis60), "6.1": ("2", Analysis61), "6.2": ("3", Analysis)}
    validator, model = versions.get(spec.get("schema_version"), (None, None))
    if (model is None or spec.get("validator_version") != validator or
            spec.get("schema") != model.model_json_schema() or
            spec.get("fingerprint") != digest({k: v for k, v in spec.items() if k not in ("fingerprint", "timeout_seconds")}) or
            (spec.get("schema_version") == "6.2" and spec.get("evidence_version") != EVIDENCE_VERSION)):
        raise V6Error("PLAN_CHANGED", "저장된 실행의 분석 규격이 지원 버전과 다르거나 손상되었습니다.")
