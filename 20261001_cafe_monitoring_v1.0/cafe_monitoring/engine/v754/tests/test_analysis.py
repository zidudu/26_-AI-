"""새 분석 경로의 회귀·실패 주입 검사. 실제 API/PowerPoint 실행을 모사하지 않습니다."""
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from v754.analysis_api import APIError, decode_response
from v754.analysis_config import load_analysis_config
from v754.analysis_engine import analyze_articles, cache_key, verify_display_artifact
from v754.analysis_prompts import make_spec, build_request
from v754.analysis_schema import source_units, validate_candidate, semantic_issues
from v754.comparison import write_comparison, inspect_v6
from v754.core import V7Error, config, digest, write_json, read_json
from v754.powerpoint import verify_analysis_table, verify_analysis_slide
from v754.tests.test_export import fixture, seal, write

BODY = ('고속도로 주행 중 엔진 경고등이 들어와 점검받았습니다.\n'
        'EGR 센서 오작동이라고 안내받았습니다.\n'
        '혹시 이거 정비 시간 얼마나 걸릴까요?\n'
        '진단업체에서는 3시간 걸린다는데 맞나요?\n'
        '근처 예약이 안돼 먼 곳으로 예약했습니다.')
NEUTRAL = '작성자는 정비 소요 시간을 문의하며, 업체에서 안내한 3시간이 맞는지 질문했습니다.'


def ref(source, text):
    return next(u['id'] for u in source_units(source) if text in u['text'])


def candidate_for(source, text=NEUTRAL):
    return {'document_type': 'question_information',
        'document_type_evidence_ids': [ref(source, '정비 시간')],
        'vehicle': {k: {'value': None, 'evidence_ids': []} for k in ('model', 'model_year', 'mileage')},
        'complaint': {'status': 'reported', 'label': 'EGR 센서 오작동·경고등',
                      'evidence_ids': [ref(source, '경고등'), ref(source, 'EGR 센서')]},
        'summary_claims': [{'text': text, 'kind': 'question',
                            'evidence_ids': [ref(source, '정비 시간'), ref(source, '3시간')]}],
        'actions': [], 'review_reasons': []}


def response_for(candidate):
    return {'id': 'offline-fixture-not-an-api-response', 'status': 'completed',
        'usage': {'input_tokens': 100, 'output_tokens': 80, 'total_tokens': 180},
        'output': [{'type': 'message', 'content': [{'type': 'output_text',
                   'text': json.dumps(candidate, ensure_ascii=False)}]}]}


def make_item(root, article_id='101'):
    d = fixture(root, article_id, summary='3시간 진단 소요가 맞는지 질문했습니다.')
    d['source'].update(title='EGR 센서 오작동 문제', body=BODY)
    d['source']['input_sha256'] = digest({k: d['source'][k] for k in ('title', 'body', 'written_at')})
    seal(d)
    draft_path, source_path = root / (article_id + '_draft.json'), root / (article_id + '_source.json')
    write(draft_path, d)
    write(source_path, d['source'])
    item = {'id': article_id, 'cafe_id': d['source']['cafe_id'], 'title': d['source']['title'],
        'url': d['source']['url'], 'written_at': d['source']['written_at'],
        'draft_path': str(draft_path), 'draft_sha256': d['artifact_sha256'],
        'refreshed_source_file': str(source_path), 'display_text': d['monitoring']['display_text'],
        'vehicle': '-', 'specs': '-', 'complaint': '-', 'same_count': '-', 'source_mode': False,
        'cafe_name': '테스트 카페', 'views': '82', 'comments': '4', 'metadata_warnings': [],
        'capture_problem': False, 'capture_notes': [], 'captures': [], 'review_notes': []}
    return item, d['source']


class FakeAnalyzer:
    def __init__(self, response, effect=None):
        self.response, self.effect, self.calls, self.closed = response, effect, [], False
    def analyze(self, request):
        self.calls.append(deepcopy(request))
        if self.effect:
            self.effect()
        return deepcopy(self.response)
    def close(self):
        self.closed = True


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        write(self.root / 'config_v6.json', {'model': 'fixture-model', 'output_dir': 'output_v6'})
        self.spec = make_spec(load_analysis_config(self.root))
        self.item, self.source = make_item(self.root)
        self.candidate = candidate_for(self.source)
        self.run_count = 0

    def run_engine(self, candidate=None, *, response=None, items=None, force=False, spec=None, effect=None):
        self.run_count += 1
        folder = self.root / 'output_v754' / f'run_{self.run_count}'
        folder.mkdir(parents=True)
        report = {}
        fake = FakeAnalyzer(response if response is not None else response_for(candidate or self.candidate), effect)
        with patch('v754.analysis_engine.load_api_key', return_value=''), redirect_stdout(io.StringIO()):
            result = analyze_articles(items or [self.item], spec or self.spec, folder, report,
                lambda: write_json(folder / 'summary.json', report), force=force,
                analyzer_factory=lambda key, cfg: fake)
        return result, report, folder, fake

    def issues(self, candidate):
        validated, bindings = validate_candidate(candidate, self.source)
        return semantic_issues(validated, bindings)

    def test_source_units_are_lossless_with_unicode_and_blank_lines(self):
        source = dict(self.source, body='\n😀한글!?\n\n' + '가'*600 + '\n끝')
        units = source_units(source)
        for field in ('title', 'body'):
            relevant = [u for u in units if u['source'] == field]
            self.assertEqual(''.join(u['text'] for u in relevant), source[field])
            self.assertTrue(all(source[field][u['start']:u['end']] == u['text'] for u in relevant))

    def test_neutral_egr_time_keeps_uncertainty(self):
        self.assertEqual(self.issues(self.candidate), [])

    def test_diagnosis_duration_is_not_inferred_from_company_word(self):
        self.candidate['summary_claims'][0]['text'] = '3시간 진단 소요가 맞는지 질문했습니다.'
        self.assertIn('TIME_SCOPE_NARROWED', [i['code'] for i in self.issues(self.candidate)])

    def test_explicit_diagnosis_duration_is_allowed(self):
        self.source['body'] = BODY.replace('진단업체에서는 3시간 걸린다는데 맞나요?', '진단 시간은 3시간이라는데 맞나요?')
        self.candidate = candidate_for(self.source, '진단 시간 3시간이 맞는지 질문했습니다.')
        self.assertEqual(self.issues(self.candidate), [])

    def test_question_cannot_silently_become_assertion(self):
        self.candidate['summary_claims'][0]['text'] = '정비에는 3시간이 걸립니다.'
        self.assertIn('QUESTION_BECAME_ASSERTION', [i['code'] for i in self.issues(self.candidate)])

    def test_added_duration_number_flagged(self):
        self.candidate['summary_claims'][0]['text'] = '정비에 5시간 걸리는지 질문했습니다.'
        self.assertIn('NUMBER_NOT_IN_EVIDENCE', [i['code'] for i in self.issues(self.candidate)])

    def test_guessing_model_from_cafe_is_flagged(self):
        self.candidate['vehicle']['model'] = {'value': '싼타페 MX5', 'evidence_ids': [ref(self.source, 'EGR 센서')]}
        self.assertIn('VEHICLE_NOT_EXPLICIT', [i['code'] for i in self.issues(self.candidate)])

    def test_invented_or_duplicate_evidence_rejected(self):
        for ids in ([], ['B9999'], ['B0001', 'B0001']):
            with self.subTest(ids=ids):
                self.candidate['summary_claims'][0]['evidence_ids'] = ids
                with self.assertRaises(V7Error):
                    validate_candidate(self.candidate, self.source)

    def test_extra_schema_fields_rejected(self):
        self.candidate['same_count'] = 1
        with self.assertRaises(V7Error) as error:
            validate_candidate(self.candidate, self.source)
        self.assertEqual(error.exception.code, 'INVALID_ANALYSIS')

    def test_promotion_is_not_used_as_consumer_complaint(self):
        self.candidate['document_type'] = 'promotion'
        self.assertIn('PROMOTION_AS_COMPLAINT', [i['code'] for i in self.issues(self.candidate)])

    def test_request_contains_full_body_and_no_keys_or_metadata(self):
        source = dict(self.source, metadata={'private': 'MUST_NOT_SEND'}, api_key='MUST_NOT_SEND')
        request = build_request(source, self.spec)
        payload = json.loads(request['input'][0]['content'])
        self.assertEqual(payload['article']['body'], BODY)
        self.assertNotIn('MUST_NOT_SEND', json.dumps(request))
        self.assertFalse(request['store'])
        self.assertTrue(request['text']['format']['strict'])

    def test_full_chain_preserves_v6_and_matches_display_artifact(self):
        before = Path(self.item['draft_path']).read_bytes()
        results, report, folder, fake = self.run_engine()
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(fake.closed)
        self.assertEqual(results[0]['display_text'], NEUTRAL)
        self.assertEqual(results[0]['same_count'], '-')
        self.assertEqual(results[0]['complaint'], '검토용 · EGR 센서 오작동·경고등')
        verify_display_artifact(results[0])
        audit = read_json(folder / 'analysis_audit.json')['items'][0]
        self.assertEqual(audit['validation']['meaning_accuracy'], 'not_reviewed')
        self.assertEqual(Path(self.item['draft_path']).read_bytes(), before)
        self.assertEqual(report['api_calls'], 1)

    def test_known_bad_summary_is_pending_without_raw_source_substitution(self):
        self.candidate['summary_claims'][0]['text'] = '3시간 진단 소요가 맞는지 질문했습니다.'
        results, report, folder, _ = self.run_engine()
        self.assertFalse(results[0]['source_mode'])
        self.assertTrue(results[0]['summary_pending'])
        self.assertNotEqual(results[0]['display_text'], BODY)
        self.assertIn('요약 확인 필요', results[0]['display_text'])
        verify_display_artifact(results[0])
        self.assertEqual(report['analysis_review_count'], 1)
        write_comparison(folder, report)
        html = (folder / 'comparison.html').read_text()
        self.assertIn('새 AI 요약 (검사 전)', html)
        self.assertIn('3시간 진단 소요', html)
        self.assertIn('TIME_SCOPE_NARROWED', json.dumps(report))

    def test_cache_reuses_response_without_analyzer_or_key(self):
        first, _, _, _ = self.run_engine()
        second, report, _, fake = self.run_engine()
        self.assertEqual(report['api_calls'], 0)
        self.assertEqual(report['analysis_cache_hits'], 1)
        self.assertEqual(fake.calls, [])
        self.assertEqual(first[0]['display_text'], second[0]['display_text'])
        self.assertEqual(report['api_usage'], [])

    def test_force_reanalysis_and_changed_prompt_each_make_new_request(self):
        self.run_engine()
        _, report, _, _ = self.run_engine(force=True)
        self.assertEqual(report['api_calls'], 1)
        cfg = load_analysis_config(self.root)
        cfg['complaint_definition'] += ' 추가 지침입니다.'
        new_spec = make_spec(cfg)
        self.assertNotEqual(cache_key(self.source, new_spec), cache_key(self.source, self.spec))
        _, report, _, _ = self.run_engine(spec=new_spec)
        self.assertEqual(report['api_calls'], 1)

    def test_tampered_and_malformed_cache_never_auto_recharge(self):
        self.run_engine()
        path = self.root / 'output_v754' / 'analysis_cache' / (cache_key(self.source, self.spec) + '.json')
        for value in ('not json', '[]', '{"response":{}}'):
            with self.subTest(value=value):
                path.write_text(value)
                results, report, _, fake = self.run_engine()
                self.assertEqual(results, [])
                self.assertEqual(report['analysis_stop_code'], 'CACHE_INVALID')
                self.assertEqual(fake.calls, [])
                self.assertEqual(report['api_calls'], 0)

    def test_schema_failure_saves_raw_response_and_usage(self):
        raw = response_for({'wrong': 'shape'})
        results, report, folder, fake = self.run_engine(response=raw)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['summary_pending'])
        verify_display_artifact(results[0])
        self.assertEqual(report['analysis_results'][0]['code'], 'INVALID_ANALYSIS')
        self.assertEqual(report['api_usage'][0]['total_tokens'], 180)
        self.assertEqual(read_json(next((folder / 'analysis').glob('*_response.json'))), raw)
        self.assertEqual(len(fake.calls), 1)

    def test_truncated_response_keeps_article_without_retry(self):
        raw = response_for(self.candidate)
        raw.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
        results, report, _, fake = self.run_engine(response=raw)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['summary_pending'])
        verify_display_artifact(results[0])
        self.assertEqual(report['analysis_results'][0]['code'], 'OUTPUT_LIMIT')
        self.assertEqual(len(fake.calls), 1)

    def test_refusal_is_not_treated_as_summary(self):
        raw = response_for(self.candidate)
        raw['output'][0]['content'] = [{'type': 'refusal', 'refusal': 'fixture'}]
        with self.assertRaises(V7Error) as error:
            decode_response(raw)
        self.assertEqual(error.exception.code, 'MODEL_REFUSAL')

    def test_unknown_api_result_stops_remaining_requests(self):
        other, _ = make_item(self.root, '102')
        def fail():
            raise APIError('API_RESULT_UNKNOWN', 'fixture timeout', True)
        results, report, folder, fake = self.run_engine(items=[self.item, other], effect=fail)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(i['summary_pending'] for i in results))
        self.assertEqual(report['analysis_results'][1]['code'], 'ANALYSIS_DEFERRED')
        self.assertEqual(report['analysis_remaining'], 0)
        self.assertEqual(report['api_calls'], 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(read_json(folder / 'analysis_audit.json')['items'][0]['request_execution'], 'unknown')

    def test_interrupt_during_request_is_recorded_as_unknown(self):
        def fail():
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_engine(effect=fail)
        folder = self.root / 'output_v754' / 'run_1'
        self.assertEqual(read_json(folder / 'analysis_audit.json')['items'][0]['request_execution'], 'unknown')
        self.assertEqual(read_json(folder / 'summary.json')['analysis_results'][0]['status'], 'interrupted')

    def test_changed_source_before_request_does_not_call_api(self):
        changed = dict(self.source, body=BODY + '수정됨')
        write_json(self.item['refreshed_source_file'], changed)
        results, report, _, fake = self.run_engine()
        self.assertEqual(results, [])
        self.assertEqual(report['analysis_results'][0]['code'], 'SOURCE_CHANGED')
        self.assertEqual(fake.calls, [])

    def test_source_change_during_api_blocks_display(self):
        def change():
            write_json(self.item['refreshed_source_file'], dict(self.source, body=BODY + '수정됨'))
        results, report, _, _ = self.run_engine(effect=change)
        self.assertEqual(results, [])
        self.assertEqual(report['analysis_results'][0]['code'], 'SOURCE_CHANGED')

    def test_oversize_source_is_retained_without_api_or_truncation(self):
        spec = dict(self.spec, max_body_chars=10)
        results, report, _, fake = self.run_engine(spec=spec)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['summary_pending'])
        verify_display_artifact(results[0])
        self.assertEqual(report['analysis_results'][0]['code'], 'BODY_TOO_LONG')
        self.assertEqual(fake.calls, [])

    def test_tampered_display_or_artifact_is_rejected(self):
        results, _, _, _ = self.run_engine()
        altered = dict(results[0], complaint='다른 분류')
        with self.assertRaises(V7Error) as error:
            verify_display_artifact(altered)
        self.assertEqual(error.exception.code, 'ANALYSIS_DISPLAY_MISMATCH')
        data = read_json(results[0]['analysis_result_file'])
        data['display']['display_text'] = '손상'
        write_json(results[0]['analysis_result_file'], data)
        with self.assertRaises(V7Error) as error:
            verify_display_artifact(results[0])
        self.assertEqual(error.exception.code, 'ANALYSIS_CHANGED')

    def test_comparison_escapes_source_markup(self):
        _, report, folder, _ = self.run_engine()
        audit = read_json(folder / 'analysis_audit.json')
        audit['items'][0]['source']['body'] = '<script>alert(1)</script>'
        write_json(folder / 'analysis_audit.json', audit)
        write_comparison(folder, report)
        html = (folder / 'comparison.html').read_text()
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_v6_audit_does_not_claim_historical_api_request_check(self):
        row = inspect_v6([self.item])[0]
        self.assertFalse(row['stored_ai_candidate_has_complaint'])
        self.assertFalse(row['actual_api_request_verified'])
        self.assertEqual(row['v752_complaint_display'], '-')

    def test_inherits_model_cafe_name_without_overwriting_v752_output(self):
        legacy = {'cafe_names': {'20179506': '싼타페 MX5 패밀리 / 클럽싼타페'},
                  'output_dir': 'output_v752', 'max_slides': 250}
        write(self.root / 'config_v752.json', legacy)
        before = (self.root / 'config_v752.json').read_bytes()
        cfg = config(self.root)
        self.assertEqual(cfg['cafe_names'], legacy['cafe_names'])
        self.assertEqual(cfg['output_dir'], 'output_v754')
        self.assertEqual(cfg['max_slides'], 250)
        self.assertEqual(load_analysis_config(self.root)['model'], 'fixture-model')
        self.assertEqual((self.root / 'config_v752.json').read_bytes(), before)

    def test_analysis_table_and_continuation_text_mismatch_detected(self):
        item = dict(self.item, complaint='검토용 · EGR 센서 오작동')
        cells = {(1, 2): '-', (1, 4): '-', (1, 6): item['complaint'], (3, 6): '-', (4, 2): NEUTRAL}
        def shape(text):
            return SimpleNamespace(TextFrame=SimpleNamespace(MarginLeft=5, MarginRight=5,
                MarginTop=3, MarginBottom=3, TextRange=SimpleNamespace(Text=text, BoundWidth=100, BoundHeight=20)))
        table = SimpleNamespace(Cell=lambda row, col: SimpleNamespace(Shape=shape(cells[row, col])),
                                Rows=SimpleNamespace(Item=lambda row: SimpleNamespace(Height=68)))
        verify_analysis_table(table, item, NEUTRAL)
        for address in cells:
            before = cells[address]
            cells[address] = '변경값'
            with self.assertRaises(V7Error):
                verify_analysis_table(table, item, NEUTRAL)
            cells[address] = before
        slide = SimpleNamespace(Shapes=SimpleNamespace(Item=lambda name:
            SimpleNamespace(Table=table) if name == 'V7_INFO_TABLE' else shape('잘못된 상세문')))
        with self.assertRaises(V7Error) as error:
            verify_analysis_slide(slide, item, {'kind': 'text', 'summary': NEUTRAL, 'text': '원문 상세'})
        self.assertEqual(error.exception.code, 'UNEXPECTED_DETAIL_SLIDE')


if __name__ == '__main__':
    unittest.main()
