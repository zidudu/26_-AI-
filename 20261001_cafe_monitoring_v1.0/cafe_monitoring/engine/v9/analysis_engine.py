"""Bounded API calls; source checks, validation and all journals stay on one thread."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from datetime import datetime
import signal
from threading import Event, Lock
from time import perf_counter

from v8.analysis_engine import (load_bound_source, source_identity, cache_key, load_cached,
    seal, build_display, pending_display, persist_display, display_fields, verify_display_artifact)
from v8.analysis_prompts import build_request
from v8.analysis_schema import validate_candidate, semantic_issues
from v754.analysis_api import Analyzer, decode_response
from v754.analysis_config import load_api_key
from v754.core import V7Error, digest, write_json
from v9.ai_provider import observed_usage as response_usage, update_usage

INTEGRITY_ERRORS = {'DRAFT_CHANGED', 'SOURCE_CHANGED', 'SOURCE_IDENTITY_MISMATCH',
                    'PERIOD_SOURCE_CHANGED', 'COLLECTION_CHANGED', 'CACHE_INVALID'}


def analyze_articles(items, spec, folder, report, checkpoint, *, concurrency=2,
                     force=False, analyzer_factory=Analyzer, provider='openai'):
    if provider not in ('openai', 'codex'):
        raise V7Error('AI_PROVIDER_ERROR', '지원하지 않는 AI 방식입니다.')
    if provider == 'codex' and (analyzer_factory is Analyzer or not spec.get('_codex_adapter')):
        raise V7Error('CODEX_NOT_BOUND', 'Codex 실행기와 캐시 식별을 먼저 연결해야 합니다.')
    if provider == 'openai' and spec.get('_codex_adapter'):
        raise V7Error('AI_PROVIDER_MISMATCH', 'Codex 요청을 API로 실행하지 않습니다.')
    call_mode = 'api' if provider == 'openai' else 'codex'
    if type(concurrency) is not int or concurrency not in (1, 2, 3):
        raise V7Error('INVALID_ANALYSIS_CONCURRENCY', 'AI 동시 분석은 1·2·3 중 하나입니다.')
    identities = [(i['cafe_id'], i['id']) for i in items]
    if len(set(identities)) != len(identities):
        raise V7Error('DUPLICATE_ARTICLE', '분석 대상 카페·게시글이 중복됩니다.')
    began = perf_counter()
    report.update(stage='ANALYZE_ARTICLES', analysis_results=[], analysis_remaining=len(items),
                  api_calls=0, codex_calls=0, ai_calls=0, ai_provider=provider,
                  analysis_cache_hits=0, analysis_review_count=0,
                  analysis_concurrency=concurrency)
    audit = {'version': '7.5.4', 'spec': spec, 'items': [], 'semantic_review': 'not_reviewed',
             'same_count_policy': 'undefined_not_counted'}
    report['analysis_audit'] = str(folder / 'analysis_audit.json')
    receipts = folder / 'analysis'
    receipts.mkdir(exist_ok=False)
    cache_dir = folder.parent / 'analysis_cache'
    cache_dir.mkdir(exist_ok=True)
    write_json(folder / 'analysis_spec.json', spec)
    output = {}
    stopped, cancelled = Event(), Event()
    peak_lock = Lock()
    active = peak = 0
    finished = set()
    jobs = []
    pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='v93-analysis')
    pending = {}

    def save():
        report['analysis_remaining'] = len(items) - len(finished)
        update_usage(report)
        report['items'] = [output[n] for n in sorted(output)]
        write_json(folder / 'analysis_audit.json', audit)
        checkpoint()

    def tag(job):
        item = job['item']
        return f"[AI {job['number']+1}/{len(items)}][{item['cafe_id']}:{item['id']}]"

    def fail(job, exc):
        result, record, source, item = (job[k] for k in ('result', 'record', 'source', 'item'))
        result.update(status='excluded', code=exc.code, message=str(exc))
        record['error'] = {'code': exc.code, 'message': str(exc)}
        if getattr(exc, 'diagnostics', None):
            result['diagnostics'] = exc.diagnostics
            record['error']['diagnostics'] = exc.diagnostics
        if getattr(exc, 'uncertain', False):
            record['request_execution'] = 'unknown'
        if source is not None and exc.code not in INTEGRITY_ERRORS:
            current = pending_display(item, source, exc.code, str(exc))
            if exc.code in ('INVALID_EVIDENCE', 'INVALID_ANALYSIS') and isinstance(job.get('raw'), dict):
                current['analysis_repair_candidate'] = deepcopy(job['raw'])
                current['analysis_repair_error'] = record['error']
                record['invalid_candidate'] = deepcopy(job['raw'])
            path = receipts / (job['stem'] + '_result.json')
            persist_display(current, path, {'identity': source_identity(source), 'candidate': None,
                                            'issues': current['analysis_issues'], 'error': record['error']})
            output[job['number']] = current
            result['status'] = 'pending_review'
            record.update(source={k: source[k] for k in ('title', 'body', 'written_at', 'url')},
                          display=display_fields(current), summary_pending=True, result_file=str(path))
            report['analysis_review_count'] += 1
        print(tag(job), '[요약 확인 필요]' if result['status'] == 'pending_review' else '[분석 제외]',
              exc.code, '/', str(exc), flush=True)
        if getattr(exc, 'stop', False) or exc.code in ('API_KEY_MISSING', 'OPENAI_MISSING', 'CACHE_INVALID'):
            report.setdefault('analysis_stop_code', exc.code)
            stopped.set()
        finished.add(job['number'])
        save()

    def accept(job, response):
        result, record, source, item = (job[k] for k in ('result', 'record', 'source', 'item'))
        identity, request = job['identity'], job['request']
        response_path = receipts / (job['stem'] + '_response.json')
        result_path = receipts / (job['stem'] + '_result.json')
        # Persist raw output/usage before parsing, including invalid responses.
        write_json(response_path, response)
        if result['mode'] in ('api', 'codex'): record['request_execution'] = 'response_received'
        record.update(response_file=str(response_path), response_sha256=digest(response),
                      response_id=response.get('id'), usage=response_usage(response))
        result['usage'] = response_usage(response)
        job['raw'] = decode_response(response)
        candidate, bindings = validate_candidate(job['raw'], source)
        issues = semantic_issues(candidate, bindings)
        source_after, _ = load_bound_source(item)
        if source_identity(source_after) != identity:
            raise V7Error('SOURCE_CHANGED', 'AI 호출 도중 원문 파일이 변경됐습니다.')
        current = build_display(item, source, candidate, bindings, issues)
        current['analysis_result_file'] = str(result_path)
        displayed = display_fields(current)
        artifact = seal({'version': '7.5.4', 'identity': identity, 'candidate': candidate,
            'bindings': bindings, 'issues': issues, 'display': displayed,
            'request_sha256': digest(request), 'response_sha256': digest(response),
            'spec_fingerprint': spec['fingerprint'], 'semantic_review': 'not_reviewed',
            'summary_pending': current['summary_pending']})
        write_json(result_path, artifact)
        current['analysis_artifact_sha256'] = artifact['artifact_sha256']
        if result['mode'] in ('api', 'codex'):
            write_json(job['cache_path'], seal({'identity': identity, 'spec_fingerprint': spec['fingerprint'],
                'request_sha256': digest(request), 'response': response, 'candidate': candidate,
                'created_at': datetime.now().astimezone().isoformat()}))
        output[job['number']] = current
        result.update(status='analyzed', complaint=current['complaint'],
                      summary_suppressed=current['source_mode'], issues=issues)
        record.update(candidate=candidate, bindings=bindings, issues=issues, display=displayed,
            summary_pending=current['summary_pending'], validation={'schema': 'pass', 'evidence_ids': 'pass',
            'semantic_rules': 'flagged' if issues else 'no_known_issue_detected', 'meaning_accuracy': 'not_reviewed'},
            result_file=str(result_path))
        if issues: report['analysis_review_count'] += 1
        finished.add(job['number'])
        print(tag(job), {'api': '새 API 응답', 'codex': '새 Codex 응답', 'cache': '저장 분석 재사용'}[result['mode']],
              '/ 불만 구분:', current['complaint'], flush=True)
        if issues: print(tag(job), '[원문 대조 필요]', ', '.join(i['code'] for i in issues), flush=True)
        save()

    def api_call(analyzer, request):
        nonlocal active, peak
        with peak_lock:
            active += 1
            peak = max(peak, active)
        try:
            return analyzer.analyze(request)
        except BaseException as exc:
            if getattr(exc, 'stop', False) or isinstance(exc, KeyboardInterrupt):
                stopped.set()
            raise
        finally:
            with peak_lock: active -= 1
            # Resource disposal is done after draining, on the coordinating thread.

    def prepare(number, item):
        job = {'number': number, 'item': item, 'source': None, 'raw': None,
               'stem': f"{item['cafe_id']}_{item['id']}", 'analyzer': None,
               'result': {'article_id': item['id'], 'cafe_id': item['cafe_id'], 'status': 'preparing'},
               'record': {'article_id': item['id'], 'cafe_id': item['cafe_id']}}
        jobs.append(job)
        report['analysis_results'].append(job['result'])
        audit['items'].append(job['record'])
        try:
            source, old = load_bound_source(item)
            job['source'] = source
            if stopped.is_set():
                raise V7Error('ANALYSIS_DEFERRED', '앞선 AI 오류로 새 분석을 중단했습니다. 게시글과 캡처를 유지합니다.')
            if len(source['body']) > spec['max_body_chars']:
                raise V7Error('BODY_TOO_LONG', '본문 길이가 분석 한도를 초과했습니다. 잘라 보내지 않았습니다.')
            if not source['body'].strip():
                raise V7Error('NO_BODY_FOR_ANALYSIS', '본문 텍스트가 없어 AI 호출을 생략했습니다. 캡처를 유지합니다.')
            request = build_request(source, spec)
            job.update(identity=source_identity(source), request=request,
                       cache_path=cache_dir / (cache_key(source, spec) + '.json'))
            request_path = receipts / (job['stem'] + '_request.json')
            write_json(request_path, request)
            job['record'].update(identity=job['identity'], source={k: source[k] for k in ('title', 'body', 'written_at', 'url')},
                old_monitoring=old['monitoring'], old_ai_candidate=old.get('ai_candidate'), old_review=old.get('review'),
                request_file=str(request_path), request_sha256=digest(request), spec_fingerprint=spec['fingerprint'])
            if job['cache_path'].is_file() and not force:
                job['result']['mode'] = 'cache'
                response = load_cached(job['cache_path'], source, spec, request)
                report['analysis_cache_hits'] += 1
                job['record']['request_execution'] = 'cached_prior_response_no_new_call'
                accept(job, response)
                return
            key = load_api_key() if provider == 'openai' else None
            if not key and analyzer_factory is Analyzer:
                raise V7Error('API_KEY_MISSING', '기존 02_setup_key_v6.bat에서 API 키를 설정하세요.')
            analyzer = analyzer_factory(key, spec)
            job['analyzer'] = analyzer
            if cancelled.is_set() or stopped.is_set():
                raise V7Error('ANALYSIS_DEFERRED', '중단 요청 이후 새 AI 호출을 시작하지 않았습니다.')
            job['result'].update(mode=call_mode, provider=provider, status='request_started')
            job['record']['request_execution'] = 'started_response_not_received'
            report[call_mode + '_calls'] += 1
            save()
            print(tag(job), '요청 시작', flush=True)
            pending[pool.submit(api_call, analyzer, request)] = job
        except V7Error as exc:
            fail(job, exc)

    def receive(future, job):
        try:
            accept(job, future.result())
        except V7Error as exc:
            fail(job, exc)
        except BaseException as exc:
            job['result'].update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                                 code=type(exc).__name__)
            if job['record'].get('request_execution') == 'started_response_not_received':
                job['record']['request_execution'] = 'unknown'
            job['record']['error'] = {'code': type(exc).__name__, 'message': '저장된 요청·응답을 확인하세요.'}
            finished.add(job['number'])
            stopped.set()
            save()
            raise
        finally:
            if job.get('analyzer') is not None:
                try: job['analyzer'].close()
                except Exception as exc: job['record']['client_close_error'] = type(exc).__name__
                job['analyzer'] = None

    old_handler = None
    try:
        old_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: (cancelled.set(), stopped.set()))
    except (ValueError, AttributeError): old_handler = None
    next_number = 0
    fatal = None
    print(f'[AI 동시 분석] {concurrency}개 / 원문·모델·근거 검증 유지', flush=True)
    try:
        save()
        while next_number < len(items) or pending:
            # Drain already-completed failures before admitting new work.
            ready = [f for f in pending if f.done()]
            for future in ready:
                receive(future, pending.pop(future))
            if cancelled.is_set():
                if provider == 'codex':
                    for running in pending.values():
                        if running.get('analyzer') is not None:
                            running['analyzer'].close()
                if not pending: break
            elif next_number < len(items) and len(pending) < concurrency:
                prepare(next_number, items[next_number])
                next_number += 1
                continue
            if pending:
                wait(tuple(pending), timeout=0.2, return_when=FIRST_COMPLETED)
            elif next_number >= len(items): break
        if cancelled.is_set(): raise KeyboardInterrupt()
        return [output[n] for n in sorted(output)]
    except BaseException as exc:
        fatal = exc
        stopped.set()
        raise
    finally:
        if provider == 'codex' and fatal is not None:
            for running in pending.values():
                if running.get('analyzer') is not None:
                    running['analyzer'].close()
        # Never leave HTTP workers using a released project lock, or lose a
        # received response just because a different worker failed/cancelled.
        for future, job in list(pending.items()):
            try: receive(future, job)
            except BaseException as exc:
                if fatal is None: fatal = exc
        pool.shutdown(wait=True, cancel_futures=False)
        for job in jobs:
            if job.get('analyzer') is not None:
                try: job['analyzer'].close()
                except Exception: pass
        if old_handler is not None: signal.signal(signal.SIGINT, old_handler)
        report['analysis_execution'] = {'concurrency': concurrency, 'peak_api_calls': peak,
            'provider': provider, 'peak_ai_calls': peak,
            'wall_seconds': round(perf_counter()-began, 3), 'cancelled': cancelled.is_set(),
            'unstarted_articles': len(items)-next_number}
        if provider == 'codex':
            report['analysis_execution']['peak_api_calls'] = 0
            report['analysis_execution']['peak_codex_calls'] = peak
        save()
