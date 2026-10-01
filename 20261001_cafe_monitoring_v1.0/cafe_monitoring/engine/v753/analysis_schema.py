"""AI 문장과 원문 근거의 연결을 검사합니다. 의미 정확성을 보장하지는 않습니다."""
from typing import Annotated, Literal
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .core import V7Error

SCHEMA_VERSION = '7.5.3-grounded-1'
IDs = Annotated[list[str], Field(max_length=20)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Fact(StrictModel):
    value: Annotated[str, Field(min_length=1, max_length=100)] | None
    evidence_ids: IDs


class Vehicle(StrictModel):
    model: Fact
    model_year: Fact
    mileage: Fact


class Claim(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=220)]
    kind: Literal['observation', 'reported_statement', 'question', 'possibility', 'action']
    evidence_ids: IDs


class Complaint(StrictModel):
    status: Literal['reported', 'not_applicable', 'unclear']
    label: Annotated[str, Field(min_length=1, max_length=35)] | None
    evidence_ids: IDs


class Action(StrictModel):
    description: Annotated[str, Field(min_length=1, max_length=200)]
    status: Literal['completed', 'planned', 'recommended', 'unavailable', 'unclear']
    evidence_ids: IDs


class Analysis(StrictModel):
    document_type: Literal['issue_experience', 'repair_review', 'question_information', 'promotion', 'other', 'unclear']
    document_type_evidence_ids: IDs
    vehicle: Vehicle
    complaint: Complaint
    summary_claims: Annotated[list[Claim], Field(min_length=1, max_length=4)]
    actions: Annotated[list[Action], Field(max_length=8)]
    review_reasons: Annotated[list[Annotated[str, Field(min_length=1, max_length=250)]], Field(max_length=12)]


def source_units(article):
    """원문을 누락 없이 나누고 유니코드 문자 기준 위치를 남깁니다."""
    units = []
    for field, prefix in (('title', 'T'), ('body', 'B')):
        text = article[field]
        start, index = 0, 1
        while start < len(text):
            limit = min(start + 240, len(text))
            piece = text[start:limit]
            match = re.search(r'\n+|(?<!\d)[.!?。！？]{1,}[ \t]*', piece)
            end = start + match.end() if match else limit
            units.append({'id': f'{prefix}{index:04d}', 'source': field,
                          'start': start, 'end': end, 'text': text[start:end]})
            start, index = end, index + 1
    return units


def validate_candidate(value, article):
    try:
        data = Analysis.model_validate(value).model_dump()
    except ValidationError as exc:
        fields = ['.'.join(map(str, e['loc'])) for e in exc.errors(include_input=False, include_url=False, include_context=False)]
        raise V7Error('INVALID_ANALYSIS', '응답 형식 오류: ' + ', '.join(fields[:8])) from None
    by_id = {u['id']: u for u in source_units(article)}
    bindings = []
    texts = [c['text'] for c in data['summary_claims']] + [a['description'] for a in data['actions']]
    texts += [f['value'] for f in data['vehicle'].values() if f['value'] is not None]
    texts += [data['complaint']['label']] if data['complaint']['label'] is not None else []
    if any(not text.strip() for text in texts):
        raise V7Error('INVALID_ANALYSIS', '공백만 있는 요약·분류·차량 값은 사용할 수 없습니다.')

    def bind(ids, path, required):
        if (required and not ids) or len(ids) != len(set(ids)) or any(
            uid not in by_id or not by_id[uid]['text'].strip() for uid in ids):
            raise V7Error('INVALID_EVIDENCE', f'{path}: 원문 근거 번호가 없거나 잘못됐습니다.')
        bindings.append({'field': path, 'evidence_ids': ids, 'quotes': [by_id[i] for i in ids]})

    bind(data['document_type_evidence_ids'], 'document_type', True)
    for field, fact in data['vehicle'].items():
        bind(fact['evidence_ids'], f'vehicle.{field}', fact['value'] is not None)
        if fact['value'] is None and fact['evidence_ids']:
            raise V7Error('INVALID_ANALYSIS', '알 수 없는 차량 값에는 근거 번호도 비워야 합니다.')
    complaint = data['complaint']
    if (complaint['status'] == 'reported') != (complaint['label'] is not None):
        raise V7Error('INVALID_ANALYSIS', '증상 분류의 상태와 label이 맞지 않습니다.')
    bind(complaint['evidence_ids'], 'complaint', complaint['status'] != 'unclear')
    for i, claim in enumerate(data['summary_claims']):
        bind(claim['evidence_ids'], f'summary_claims[{i}]', True)
    for i, action in enumerate(data['actions']):
        bind(action['evidence_ids'], f'actions[{i}]', True)
    return data, bindings


def semantic_issues(data, bindings):
    """알려진 과도한 해석을 찾는 제한된 규칙입니다. 전체 의미를 증명하지 않습니다."""
    corpus = {b['field']: '\n'.join(q['text'] for q in b['quotes']) for b in bindings}
    issues = []
    def issue(field, code, message):
        issues.append({'field': field, 'code': code, 'message': message})

    diagnosis_scope = r'진단\s*(?:시간|소요|에만|만\s*\d|에\s*\d|하는\s*데)'
    duration = r'\d+(?:\.\d+)?\s*(?:시간|분|일)'
    for i, claim in enumerate(data['summary_claims']):
        field = f'summary_claims[{i}]'
        text, evidence = claim['text'], corpus[field]
        if re.search(diagnosis_scope, text) and re.search(duration, text) and not re.search(diagnosis_scope, evidence):
            issue(field, 'TIME_SCOPE_NARROWED', '업체 안내 시간을 진단 시간으로 좁혀 해석했는지 확인하세요.')
        if re.search(r'(?:주행|운전)\s*중', text) and not re.search(r'(?:주행|운전)\s*중', evidence):
            issue(field, 'DRIVING_CONTEXT_ADDED', '연결된 원문에 없는 주행 중 상황을 추가했습니다.')
        if claim['kind'] == 'question' and not re.search(r'문의|질문|물었|묻|궁금|고민|여부|맞는지|\?', text):
            issue(field, 'QUESTION_BECAME_ASSERTION', '질문을 사실로 바꾸어 표현했는지 확인하세요.')
        if claim['kind'] == 'possibility' and not re.search(r'추정|추측|가능|의심|수 있|때문인지|그런지', text):
            issue(field, 'POSSIBILITY_BECAME_ASSERTION', '추측이나 가능성을 단정했는지 확인하세요.')
        # 정규화한 숫자가 연결된 근거에 없는 경우만 표시를 보류합니다.
        numbers = re.findall(r'\d+(?:[.,]\d+)*', text)
        source_numbers = {n.replace(',', '') for n in re.findall(r'\d+(?:[.,]\d+)*', evidence)}
        if any(n.replace(',', '') not in source_numbers for n in numbers):
            issue(field, 'NUMBER_NOT_IN_EVIDENCE', '연결된 근거에서 요약의 숫자를 확인하지 못했습니다.')
    for field, fact in data['vehicle'].items():
        if fact['value'] and re.sub(r'\s+', '', fact['value']) not in re.sub(r'\s+', '', corpus[f'vehicle.{field}']):
            issue(f'vehicle.{field}', 'VEHICLE_NOT_EXPLICIT', '차량 정보가 연결된 원문에 명시돼 있는지 확인하세요.')
    if data['document_type'] == 'promotion' and data['complaint']['status'] == 'reported':
        issue('complaint', 'PROMOTION_AS_COMPLAINT', '홍보의 일반 설명을 소비자 불만 사례로 분류하지 않습니다.')
    if data['complaint']['status'] == 'unclear':
        issue('complaint', 'COMPLAINT_UNCLEAR', '불만의 대상·증상을 분류할 근거가 부족합니다.')
    return issues
