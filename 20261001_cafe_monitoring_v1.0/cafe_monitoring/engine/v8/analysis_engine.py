"""새 원문 -> 실제 요청/응답 보관 -> 근거 검증 -> 새 표시값. V6는 읽기 전용."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from v754.analysis_api import Analyzer, decode_response, response_usage
from v754.analysis_config import load_api_key
from v8.analysis_prompts import build_request
from v8.analysis_schema import source_units, validate_candidate, semantic_issues
from v754.core import V7Error, digest, read_json, validate_draft, write_json


def source_identity(source):
    return {'cafe_id': source['cafe_id'], 'article_id': source['article_id'],
            'input_sha256': digest({k: source[k] for k in ('title', 'body', 'written_at')})}


def load_bound_source(item):
    if item.get('source_kind') == 'period_collection':
        from v754.period_sources import load_period_source
        return load_period_source(item), {'monitoring': {}, 'review': {}}
    old = read_json(item['draft_path'])
    validate_draft(old)
    if old['artifact_sha256'] != item['draft_sha256']:
        raise V7Error('DRAFT_CHANGED', 'V6 초안이 선정 이후 변경됐습니다.')
    source = read_json(item['refreshed_source_file'])
    identity = source_identity(source)
    if identity != {'cafe_id': item['cafe_id'], 'article_id': item['id'], 'input_sha256': old['source']['input_sha256']}:
        raise V7Error('SOURCE_CHANGED', '재수집 원문과 V6 비교 대상이 일치하지 않습니다.')
    return source, old


def cache_key(source, spec):
    return digest({'identity': source_identity(source), 'spec': spec['fingerprint']})


def seal(data):
    data['artifact_sha256'] = digest({k: v for k, v in data.items() if k != 'artifact_sha256'})
    return data


def load_cached(path, source, spec, request):
    try:
        cached = read_json(path)
    except V7Error:
        raise V7Error('CACHE_INVALID', '기존 분석 캐시를 읽지 못했습니다. 자동 재과금하지 않습니다.') from None
    if not isinstance(cached, dict) or not isinstance(cached.get('response'), dict):
        raise V7Error('CACHE_INVALID', '기존 분석 캐시 형식이 잘못됐습니다. 자동 재과금하지 않습니다.')
    if (cached.get('artifact_sha256') != digest({k: v for k, v in cached.items() if k != 'artifact_sha256'})
            or cached.get('identity') != source_identity(source)
            or cached.get('spec_fingerprint') != spec['fingerprint']
            or cached.get('request_sha256') != digest(request)):
        raise V7Error('CACHE_INVALID', '기존 분석 캐시의 원문·지침·무결성 검증에 실패했습니다. 자동 재과금하지 않습니다.')
    response = cached['response']
    try:
        candidate, _ = validate_candidate(decode_response(response), source)
    except V7Error:
        raise V7Error('CACHE_INVALID', '캐시 응답의 분석·근거 검증에 실패했습니다. 자동 재과금하지 않습니다.') from None
    if candidate != cached.get('candidate'):
        raise V7Error('CACHE_INVALID', '캐시의 응답과 분석값이 다릅니다.')
    return response


def build_display(item, source, candidate, bindings, issues):
    updated = deepcopy(item)
    bad_fields = {i['field'] for i in issues}
    suppressed = any(f.startswith('summary_claims') for f in bad_fields)
    ai_summary = ' '.join(c['text'].strip() for c in candidate['summary_claims'])
    complaint = candidate['complaint']
    if 'complaint' in bad_fields or complaint['status'] == 'unclear':
        label = '분류 확인 필요'
    elif complaint['status'] == 'not_applicable':
        label = ('해당 없음 · 홍보' if candidate['document_type'] == 'promotion' else
                 '사용 문의 · 불만 해당 없음' if candidate['document_type'] == 'question_information' else '해당 없음')
    else:
        label = '검토용 · ' + complaint['label'].strip()
    vehicle = {k: f['value'] if f'vehicle.{k}' not in bad_fields else None for k, f in candidate['vehicle'].items()}
    specs = [f'{label}: {vehicle[k]}' for k, label in (('model_year', '연식'), ('mileage', '주행거리')) if vehicle[k]]
    updated.update(
        display_text='요약 확인 필요 — 질문·추측·숫자 표현을 원문과 대조하세요.' if suppressed else ai_summary,
        summary_pending=suppressed, ai_summary=ai_summary, source_mode=False, complaint=label, same_count='-',
        vehicle=vehicle['model'] or '-', specs=' / '.join(specs) or '-',
        document_type=candidate['document_type'],
        analysis_version='8.2.0', analysis_candidate=candidate,
        analysis_issues=issues, analysis_bindings=bindings,
        analysis_input_sha256=source_identity(source)['input_sha256'],
        old_display_text=item.get('display_text', ''), review_status='not_reviewed')
    # 이전 V6의 검토 메모는 비교 보고서에 보존하고 새 초안의 메모와 혼동하지 않습니다.
    updated['old_review_notes'] = list(item.get('review_notes', []))
    updated['review_notes'] = [
        'V8.2: 저장 원문을 근거로 AI 요약. 이미지와 댓글 본문은 AI 입력에서 제외.',
        '불만 구분은 대상·증상 중심의 검토용 분류이며 회사 공식 분류가 아닙니다.',
        '동일건수는 집계 기준이 정해지지 않아 계산하지 않았습니다.',
        '문자열·근거 연결 검증과 의미 정확성은 다릅니다. 직원 원문 대조가 필요합니다.',
        f"카페 이름 근거: {item.get('metadata', {}).get('cafe_name_source', 'unverified')}",
        *item.get('metadata_warnings', []), *candidate['review_reasons'],
        *[i['message'] for i in issues]]
    return updated


def analyze_articles(items, spec, folder, report, checkpoint, *, force=False, analyzer_factory=Analyzer):
    report['stage'] = 'ANALYZE_ARTICLES'
    report['analysis_results'] = []
    report['analysis_remaining'] = len(items)
    report['api_calls'] = 0
    report['analysis_cache_hits'] = 0
    report['analysis_review_count'] = 0
    audit = {'version': '7.5.4', 'spec': spec, 'items': [],
             'semantic_review': 'not_reviewed', 'same_count_policy': 'undefined_not_counted'}
    report['analysis_audit'] = str(folder / 'analysis_audit.json')
    receipt_dir = folder / 'analysis'
    receipt_dir.mkdir(exist_ok=False)
    cache_dir = folder.parent / 'analysis_cache'
    cache_dir.mkdir(exist_ok=True)
    write_json(folder / 'analysis_spec.json', spec)
    output, analyzer = [], None

    def save():
        report['analysis_remaining'] = len(items) - len(report['analysis_results'])
        report['api_usage'] = [r['usage'] for r in report['analysis_results'] if r.get('mode') == 'api' and r.get('usage')]
        write_json(folder / 'analysis_audit.json', audit)
        report['items'] = output
        checkpoint()

    save()
    try:
        for number, item in enumerate(items, 1):
            stem = f"{item['cafe_id']}_{item['id']}"
            result = {'article_id': item['id'], 'cafe_id': item['cafe_id'], 'status': 'preparing'}
            record = {'article_id': item['id'], 'cafe_id': item['cafe_id']}
            report['analysis_results'].append(result)
            audit['items'].append(record)
            print(f"[AI 분석 {number}/{len(items)}] {item['id']}", flush=True)
            source = None
            raw_candidate = None
            try:
                source, old = load_bound_source(item)
                if report.get('analysis_stop_code'):
                    raise V7Error('ANALYSIS_DEFERRED', '앞선 API 오류로 추가 호출을 중단했습니다. 게시글과 캡처는 보존합니다.')
                if len(source['body']) > spec['max_body_chars']:
                    raise V7Error('BODY_TOO_LONG', '본문 길이가 분석 설정 한도를 초과했습니다. 잘라 보내지 않았습니다.')
                if not source['body'].strip():
                    raise V7Error('NO_BODY_FOR_ANALYSIS', '본문 텍스트가 없는 이미지·영상 글입니다. 캡처를 유지하고 AI 호출을 생략했습니다.')
                identity = source_identity(source)
                request = build_request(source, spec)
                request_path = receipt_dir / (stem + '_request.json')
                response_path = receipt_dir / (stem + '_response.json')
                result_path = receipt_dir / (stem + '_result.json')
                cache_path = cache_dir / (cache_key(source, spec) + '.json')
                write_json(request_path, request)
                record.update(identity=identity, source={k: source[k] for k in ('title', 'body', 'written_at', 'url')},
                              old_monitoring=old['monitoring'], old_ai_candidate=old.get('ai_candidate'),
                              old_review=old.get('review'), request_file=str(request_path),
                              request_sha256=digest(request), spec_fingerprint=spec['fingerprint'])
                if cache_path.is_file() and not force:
                    result['mode'] = 'cache'
                    response = load_cached(cache_path, source, spec, request)
                    report['analysis_cache_hits'] += 1
                    record['request_execution'] = 'cached_prior_response_no_new_call'
                else:
                    result['mode'] = 'api'
                    if analyzer is None:
                        key = load_api_key()
                        if not key and analyzer_factory is Analyzer:
                            raise V7Error('API_KEY_MISSING', '기존 02_setup_key_v6.bat에서 API 키를 설정하세요.')
                        analyzer = analyzer_factory(key, spec)
                    result['status'] = 'request_started'
                    record['request_execution'] = 'started_response_not_received'
                    report['api_calls'] += 1
                    save()
                    response = analyzer.analyze(request)
                    record['request_execution'] = 'response_received'
                # 해석 전에 원본 응답부터 저장합니다. 실패 응답의 사용량도 보존합니다.
                write_json(response_path, response)
                record.update(response_file=str(response_path), response_sha256=digest(response),
                              response_id=response.get('id'), usage=response_usage(response))
                result['usage'] = response_usage(response)
                raw_candidate = decode_response(response)
                candidate, bindings = validate_candidate(raw_candidate, source)
                issues = semantic_issues(candidate, bindings)
                # 분석 도중 파일이 변경된 경우 표시값과 캡처의 연결을 허용하지 않습니다.
                source_after, _ = load_bound_source(item)
                if source_identity(source_after) != identity:
                    raise V7Error('SOURCE_CHANGED', 'AI 호출 도중 원문 파일이 변경됐습니다.')
                current = build_display(item, source, candidate, bindings, issues)
                current['analysis_result_file'] = str(result_path)
                displayed = {k: current[k] for k in ('vehicle', 'specs', 'complaint', 'same_count', 'display_text', 'source_mode')}
                artifact = seal({'version': '7.5.4', 'identity': identity, 'candidate': candidate,
                    'bindings': bindings, 'issues': issues, 'display': displayed,
                    'request_sha256': digest(request), 'response_sha256': digest(response),
                    'spec_fingerprint': spec['fingerprint'], 'semantic_review': 'not_reviewed',
                    'summary_pending': current['summary_pending']})
                write_json(result_path, artifact)
                current['analysis_artifact_sha256'] = artifact['artifact_sha256']
                if result['mode'] == 'api':
                    write_json(cache_path, seal({'identity': identity, 'spec_fingerprint': spec['fingerprint'],
                        'request_sha256': digest(request), 'response': response, 'candidate': candidate,
                        'created_at': datetime.now().astimezone().isoformat()}))
                output.append(current)
                result.update(status='analyzed', complaint=current['complaint'], summary_suppressed=current['source_mode'], issues=issues)
                record.update(candidate=candidate, bindings=bindings, issues=issues, display=displayed, summary_pending=current['summary_pending'],
                              validation={'schema': 'pass', 'evidence_ids': 'pass',
                                          'semantic_rules': 'flagged' if issues else 'no_known_issue_detected',
                                          'meaning_accuracy': 'not_reviewed'}, result_file=str(result_path))
                if issues:
                    report['analysis_review_count'] += 1
                print(f"  {('새 API 응답' if result['mode'] == 'api' else '저장된 새 분석 재사용')} / 불만 구분: {current['complaint']}", flush=True)
                if issues:
                    print('  [원문 대조 필요] ' + ', '.join(i['code'] for i in issues), flush=True)
            except V7Error as exc:
                result.update(status='excluded', code=exc.code, message=str(exc))
                record['error'] = {'code': exc.code, 'message': str(exc)}
                if getattr(exc, 'diagnostics', None):
                    result['diagnostics'] = exc.diagnostics
                    record['error']['diagnostics'] = exc.diagnostics
                    detail = exc.diagnostics
                    print(f"  [API 진단] 경과 {detail['elapsed_seconds']}초 / 대기 설정 {detail['timeout_seconds']}초 / "
                          + ' → '.join(detail['exception_chain']), flush=True)
                if getattr(exc, 'uncertain', False):
                    record['request_execution'] = 'unknown'
                # 유효한 원문을 읽은 글은 요약 실패가 나더라도 PPT에서 누락하지 않습니다.
                integrity_errors = {'DRAFT_CHANGED', 'SOURCE_CHANGED', 'SOURCE_IDENTITY_MISMATCH',
                                    'PERIOD_SOURCE_CHANGED', 'COLLECTION_CHANGED', 'CACHE_INVALID'}
                if source is not None and exc.code not in integrity_errors:
                    current = pending_display(item, source, exc.code, str(exc))
                    # Invalid IDs are never accepted or guessed. Keep the received
                    # candidate separately for one bounded corrective request.
                    if exc.code in ('INVALID_EVIDENCE', 'INVALID_ANALYSIS') and isinstance(raw_candidate, dict):
                        current['analysis_repair_candidate'] = deepcopy(raw_candidate)
                        current['analysis_repair_error'] = record['error']
                        record['invalid_candidate'] = deepcopy(raw_candidate)
                    path = receipt_dir / (stem + '_result.json')
                    persist_display(current, path, {'identity': source_identity(source), 'candidate': None,
                                    'issues': current['analysis_issues'], 'error': record['error']})
                    output.append(current)
                    result['status'] = 'pending_review'
                    record.update(source={k: source[k] for k in ('title', 'body', 'written_at', 'url')},
                                  display=display_fields(current), summary_pending=True, result_file=str(path))
                    report['analysis_review_count'] += 1
                print(f"  [{'요약 확인 필요·게시글 유지' if result['status'] == 'pending_review' else '분석 제외'}] {exc.code} / {exc}", flush=True)
                if getattr(exc, 'stop', False) or exc.code in ('API_KEY_MISSING', 'OPENAI_MISSING', 'CACHE_INVALID'):
                    report['analysis_stop_code'] = exc.code
            except BaseException as exc:
                # 응답 수신 도중 중단되면 과금/완료 여부를 성공이나 실패로 단정하지 않습니다.
                result.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                              code=type(exc).__name__)
                if record.get('request_execution') == 'started_response_not_received':
                    record['request_execution'] = 'unknown'
                record['error'] = {'code': type(exc).__name__, 'message': '실행이 중단됐습니다. 저장된 요청·응답을 확인하세요.'}
                raise
            save()
        return output
    finally:
        save()
        if analyzer is not None:
            analyzer.close()


def verify_display_artifact(item):
    data = read_json(item['analysis_result_file'])
    if (data.get('artifact_sha256') != item.get('analysis_artifact_sha256')
            or data.get('artifact_sha256') != digest({k: v for k, v in data.items() if k != 'artifact_sha256'})):
        raise V7Error('ANALYSIS_CHANGED', '검증 후 분석 파일이 변경됐습니다.')
    if data['identity'] != {'cafe_id': item['cafe_id'], 'article_id': item['id'], 'input_sha256': item['analysis_input_sha256']}:
        raise V7Error('ANALYSIS_WRONG_ARTICLE', '분석 결과의 게시글 식별자가 다릅니다.')
    if ('candidate' in data and 'analysis_candidate' in item and data['candidate'] != item['analysis_candidate']):
        raise V7Error('ANALYSIS_CHANGED', '저장 분석과 재사용 후보가 다릅니다.')
    if ('summary_pending' in data and data['summary_pending'] != item.get('summary_pending', False)):
        raise V7Error('ANALYSIS_CHANGED', '저장된 요약 검토 상태와 표시 상태가 다릅니다.')
    if any(item[k] != v for k, v in data['display'].items()):
        raise V7Error('ANALYSIS_DISPLAY_MISMATCH', '저장된 분석 표시값과 PPT 입력이 다릅니다.')


DISPLAY_KEYS = ('vehicle', 'specs', 'complaint', 'same_count', 'display_text', 'source_mode')


def display_fields(item):
    return {k: item[k] for k in DISPLAY_KEYS}


def persist_display(item, path, artifact):
    data = seal({**artifact, 'version': '7.5.4', 'build': '8.2.0',
                 'display': display_fields(item), 'summary_pending': item.get('summary_pending', False),
                 'semantic_review': 'not_reviewed'})
    write_json(path, data)
    item['analysis_result_file'] = str(path)
    item['analysis_artifact_sha256'] = data['artifact_sha256']


def pending_display(item, source, code, message):
    current = deepcopy(item)
    media = not source['body'].strip() and source.get('media')
    text = '이미지·영상 중심 게시글 — 내용 확인 필요.' if media else '요약 확인 필요 — 저장된 원문과 분석 기록을 확인하세요.'
    current.update(vehicle='-', specs='-', complaint='분류 확인 필요', same_count='-',
                   display_text=text, source_mode=False, summary_pending=True,
                   analysis_input_sha256=source_identity(source)['input_sha256'],
                   analysis_version='8.2.0', analysis_candidate=None, analysis_bindings=[],
                   analysis_issues=[{'field': 'summary', 'code': code, 'message': message}],
                   review_status='not_reviewed')
    current['review_notes'] = [*item.get('review_notes', []), message]
    return current


def update_display_record(item, folder, report, *, reason=None):
    """변경된 표시값은 새 실행에만 저장하고 이전 원문·분석은 보존합니다."""
    previous = read_json(item['analysis_result_file'])
    path = folder / 'analysis' / f"{item['cafe_id']}_{item['id']}_display_result.json"
    path.parent.mkdir(exist_ok=True)
    repair = item.get('summary_repair') or {}
    linkage = {k: repair[k] for k in ('request_sha256', 'response_sha256') if repair.get(k)}
    persist_display(item, path, {**previous, **linkage, 'candidate': item.get('analysis_candidate'),
                    'bindings': item.get('analysis_bindings', []), 'issues': item.get('analysis_issues', []),
                    'previous_artifact_sha256': item.get('analysis_artifact_sha256'),
                    'display_reason': reason, 'summary_repair': repair or None})
    audit_path = folder / 'analysis_audit.json'
    audit = read_json(audit_path) if audit_path.exists() else {'version': '7.5.4', 'items': []}
    record = next((r for r in audit['items'] if r.get('article_id') == item['id'] and r.get('cafe_id') == item['cafe_id']), None)
    if record is None:
        source, old = load_bound_source(item)
        record = {'article_id': item['id'], 'cafe_id': item['cafe_id'],
                  'source': {k: source[k] for k in ('title', 'body', 'written_at', 'url')},
                  'old_monitoring': old.get('monitoring', {})}
        audit['items'].append(record)
    if repair and 'initial_analysis' not in record:
        record['initial_analysis'] = deepcopy(record)
    record.update({k: repair[k] for k in ('request_file', 'response_file', 'request_sha256', 'response_sha256', 'usage') if k in repair})
    record.update(display=display_fields(item), candidate=item.get('analysis_candidate'),
                  bindings=item.get('analysis_bindings', []),
                  issues=item.get('analysis_issues', []), summary_pending=item.get('summary_pending', False),
                  result_file=str(path), display_reason=reason, summary_repair=item.get('summary_repair'),
                  prior_candidate=item.get('prior_analysis_candidate'))
    write_json(audit_path, audit)
    report['analysis_audit'] = str(audit_path)


def mark_summary_pending(item, folder, report, reason):
    item.update(display_text='요약 확인 필요 — 한 칸에 들어가는 요약을 확정하지 못했습니다.',
                source_mode=False, summary_pending=True)
    issue = {'field': 'summary', 'code': reason, 'message': '자동 상세 슬라이드는 만들지 않았습니다. 전체 분석은 비교 보고서에 보존했습니다.'}
    if not any(i['code'] == reason for i in item.get('analysis_issues', [])):
        item.setdefault('analysis_issues', []).append(issue)
    item.setdefault('review_notes', []).append(issue['message'])
    update_display_record(item, folder, report, reason=reason)
