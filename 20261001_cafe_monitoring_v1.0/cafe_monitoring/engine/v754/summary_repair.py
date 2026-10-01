"""검토 경고/칸 넘침의 재요약은 글당 최대 한 번. 불확실한 API 실패는 재시도하지 않습니다."""
from copy import deepcopy
from pathlib import Path
from .analysis_api import Analyzer, decode_response, response_usage
from .analysis_config import load_api_key
from .analysis_prompts import build_request
from .analysis_schema import validate_candidate, semantic_issues
from .analysis_engine import (build_display, cache_key, load_bound_source, load_cached,
    seal, source_identity, update_display_record)
from .core import V7Error, digest, read_json, write_json


class SummaryRepair:
    def __init__(self, spec, folder, report, checkpoint, analyzer_factory=Analyzer):
        self.spec, self.folder, self.report, self.checkpoint = spec, Path(folder), report, checkpoint
        self.factory = analyzer_factory
        self.used = set()

    def __call__(self, article, reason):
        key = (article['cafe_id'], article['id'])
        if key in self.used or self.report.get('analysis_stop_code'):
            return False
        # 이미지 전용/미분석 글에 요약 재요청을 보내지 않습니다.
        if not article.get('analysis_candidate'):
            return False
        self.used.add(key)
        source, _ = load_bound_source(article)
        candidate_before = deepcopy(article['analysis_candidate'])
        request = build_request(source, self.spec)
        request['instructions'] += ('\n이 요청은 한 번만 허용된 요약 수정입니다. 전체 summary_claims를 공백 포함 160자 이내로 다시 작성하세요. '
            '증상·상황 및 실제 조치/결과/질문을 짧고 완결된 1~3문장으로 보존하세요. '
            '원인이나 해결 여부를 추측하지 말고 질문과 가능성을 유지하세요. 숫자는 원문 표기를 유지하세요. '
            '다른 필드도 원문 근거를 연결하여 같은 JSON 형식으로 반환하세요.')
        repair_key = digest({'initial': cache_key(source, self.spec), 'request': request, 'purpose': 'compact_summary_v1'})
        cache_path = self.folder.parent / 'analysis_cache' / (repair_key + '_compact.json')
        stem = self.folder / 'analysis' / f"{article['cafe_id']}_{article['id']}_compact"
        request_path, response_path = Path(str(stem)+'_request.json'), Path(str(stem)+'_response.json')
        request_path.parent.mkdir(exist_ok=True)
        write_json(request_path, request)
        entry = {'article_id': article['id'], 'cafe_id': article['cafe_id'], 'reason': reason,
                 'status': 'preparing', 'request_file': str(request_path),
                 'request_sha256': digest(request), 'identity': source_identity(source),
                 'prior_candidate': candidate_before, 'api_calls': 0}
        self.report.setdefault('summary_repairs', []).append(entry)
        client = None
        try:
            if cache_path.exists():
                response = load_cached(cache_path, source, self.spec, request)
                entry.update(mode='cache', status='cached_response')
                self.report['analysis_cache_hits'] = self.report.get('analysis_cache_hits', 0) + 1
            else:
                api_key = load_api_key()
                if not api_key and self.factory is Analyzer:
                    raise V7Error('API_KEY_MISSING', '재요약 API 키가 없습니다.')
                client = self.factory(api_key, self.spec)
                entry.update(mode='api', status='request_started', api_calls=1)
                self.report['api_calls'] = self.report.get('api_calls', 0) + 1
                self.checkpoint()
                print(f"[요약 수정] 게시글 {article['id']} / 추가 API 1회 / {reason}", flush=True)
                response = client.analyze(request)
            # 오류 응답도 먼저 기록합니다.
            write_json(response_path, response)
            entry.update(status='response_received', response_file=str(response_path),
                         response_sha256=digest(response), usage=response_usage(response))
            data, bindings = validate_candidate(decode_response(response), source)
            issues = semantic_issues(data, bindings)
            if source_identity(load_bound_source(article)[0]) != source_identity(source):
                raise V7Error('SOURCE_CHANGED', '재요약 도중 원문이 변경됐습니다.')
            # 실패 응답은 캐시로 승인하지 않습니다. 의미 검토 경고는 응답과 함께 남깁니다.
            if entry['mode'] == 'api':
                cache_path.parent.mkdir(exist_ok=True)
                write_json(cache_path, seal({'identity': source_identity(source), 'spec_fingerprint': self.spec['fingerprint'],
                    'request_sha256': digest(request), 'response': response, 'candidate': data}))
            entry.update(status='validated', issues=issues)
            current = build_display(article, source, data, bindings, issues)
            current['summary_repair'] = {k:v for k,v in entry.items() if k != 'prior_candidate'}
            current['prior_analysis_candidate'] = candidate_before
            article.update(current)
            update_display_record(article, self.folder, self.report, reason=reason)
            return not article['summary_pending']
        except V7Error as exc:
            entry.update(status='failed', code=exc.code, message=str(exc))
            if getattr(exc, 'uncertain', False):
                entry['status'] = 'response_unknown'
            if getattr(exc, 'stop', False) or exc.code in ('API_KEY_MISSING', 'CACHE_INVALID'):
                self.report['analysis_stop_code'] = exc.code
            if exc.code in ('SOURCE_CHANGED', 'ANALYSIS_CHANGED'):
                raise
            return False
        finally:
            if entry['status'] == 'request_started':
                entry['status'] = 'response_unknown'
            if client:
                client.close()
            usage = [r['usage'] for r in self.report.get('analysis_results', []) if r.get('mode') == 'api' and r.get('usage')]
            usage += [r['usage'] for r in self.report.get('summary_repairs', []) if r.get('mode') == 'api' and r.get('usage')]
            self.report['api_usage'] = usage
            write_json(self.folder / 'summary_repair_audit.json', self.report.get('summary_repairs', []))
            self.checkpoint()
