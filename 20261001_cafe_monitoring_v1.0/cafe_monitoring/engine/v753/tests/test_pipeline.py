"""CLI-분석-저장-PPT 입력 연결 검사. 브라우저/API/Office는 명시적 모사 경계입니다."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v753 import main_v753 as cli
from v753.analysis_engine import analyze_articles, verify_display_artifact
from v753.core import config, digest, read_json, write_json, V7Error
from v753.tests.test_export import test_key, write
from v753.tests.test_analysis import make_item, candidate_for, response_for, FakeAnalyzer, NEUTRAL


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        write(self.root / 'config_v6.json', {'model': 'fixture-model'})
        write(self.root / 'config_v752.json', {'cafe_names': {'20179506': '테스트 카페'}})
        self.cfg = config(self.root)
        self.item, self.source = make_item(self.root)
        self.folder = self.root / 'output_v6' / 'drafts' / 'run_v6'
        name = test_key() + '.json'
        write(self.folder / name, read_json(self.item['draft_path']))
        write(self.folder / 'summary.json', {'run_id': 'run_v6', 'saved': 1,
                'items': [{'file': name, 'status': 'draft_saved'}]})
        self.args = SimpleNamespace(command='export', run='run_v6', count=1, prompt=False, article=None, reanalyze=False)
        self.rendered = []
        self.api = FakeAnalyzer(response_for(candidate_for(self.source)))

    def refresh(self, articles, cfg, folder, report, checkpoint, root):
        output = []
        for article in articles:
            item = deepcopy(article)
            item.update(refreshed_source_file=self.item['refreshed_source_file'],
                        metadata_warnings=[], metadata={}, capture_problem=False, comments='4', views='82')
            output.append(item)
        report['items'] = output
        report['refresh_results'] = [{'status': 'refreshed'} for _ in output]
        checkpoint()
        return output

    def analyze(self, items, spec, folder, report, checkpoint, **kwargs):
        return analyze_articles(items, spec, folder, report, checkpoint,
                                analyzer_factory=lambda key, cfg: self.api, **kwargs)

    def render(self, items, cfg, folder, report, checkpoint):
        for item in items:
            verify_display_artifact(item)
        self.rendered.extend(deepcopy(items))
        report.update(slides=len(items), pptx=str(folder / 'monitoring.pptx'),
                      renderer_execution='mock_only_no_native_office')
        checkpoint()

    def patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(cli, 'ROOT', self.root))
        stack.enter_context(patch('v753.powerpoint.check_office', return_value='mock'))
        stack.enter_context(patch('v753.powerpoint.render', side_effect=self.render))
        stack.enter_context(patch('v753.refresh.browser_config', return_value={}))
        stack.enter_context(patch('v753.refresh.refresh_with_browser', side_effect=self.refresh))
        stack.enter_context(patch('v753.analysis_engine.analyze_articles', side_effect=self.analyze))
        stack.enter_context(patch('v753.analysis_engine.load_api_key', return_value=''))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return stack

    def test_export_routes_new_analysis_to_renderer_and_reports(self):
        before = {p: p.read_bytes() for p in self.folder.glob('*.json')}
        with self.patches():
            code = cli.do_export(self.args, self.cfg)
        self.assertEqual(code, 0)
        self.assertEqual(self.rendered[0]['display_text'], NEUTRAL)
        self.assertEqual(self.rendered[0]['comments'], '4')
        self.assertTrue(self.rendered[0]['complaint'].startswith('검토용'))
        report_path = next(self.cfg['out_root'].glob('*/summary.json'))
        report = read_json(report_path)
        self.assertEqual(report['api_calls'], 1)
        self.assertEqual(report['status'], 'completed')
        self.assertFalse(report['reopened_structure_verified'])  # mock never proves Office
        self.assertTrue((report_path.parent / 'comparison.html').is_file())
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_ppt_failure_retains_analysis_and_can_rebuild_without_api(self):
        def broken_render(*args):
            raise V7Error('PPT_FIXTURE_FAILURE', '모사한 Office 저장 실패')
        with self.patches(), patch('v753.powerpoint.render', side_effect=broken_render):
            with self.assertRaises(V7Error):
                cli.do_export(self.args, self.cfg)
        prior_path = next(self.cfg['out_root'].glob('*/summary.json'))
        prior = read_json(prior_path)
        self.assertEqual(prior['status'], 'failed')
        self.assertEqual(len(prior['items']), 1)
        before = prior_path.read_bytes()
        args = SimpleNamespace(run=prior_path.parent.name, prompt=False)
        with self.patches(), patch('v753.core.capture_files', return_value=([{'index': 1}], [])), \
             patch('v753.analysis_engine.analyze_articles', side_effect=AssertionError('rebuild must not analyze')), \
             patch('v753.refresh.refresh_with_browser', side_effect=AssertionError('rebuild must not browse')):
            code = cli.rebuild(args, self.cfg)
        self.assertEqual(code, 0)
        self.assertEqual(prior_path.read_bytes(), before)
        rebuilt = read_json(next(self.cfg['out_root'].glob('rebuild_*/summary.json')))
        self.assertEqual(rebuilt['api_calls'], 0)
        self.assertEqual(rebuilt['items'][0]['display_text'], NEUTRAL)

    def test_rejected_summary_yields_review_status_and_full_source_ppt_input(self):
        candidate = candidate_for(self.source, '3시간 진단 소요가 맞는지 질문했습니다.')
        self.api.response = response_for(candidate)
        with self.patches():
            code = cli.do_export(self.args, self.cfg)
        self.assertEqual(code, 2)
        self.assertTrue(self.rendered[0]['source_mode'])
        self.assertEqual(self.rendered[0]['display_text'], self.source['body'])

    def test_select_test_article_by_id_or_number(self):
        items = [dict(self.item, id='1'), dict(self.item, id='2')]
        args = SimpleNamespace(command='test', article='2', prompt=False)
        self.assertEqual(cli.select_articles(items, args)[0]['id'], '2')
        args.article, args.prompt = None, True
        with patch('builtins.input', return_value='2'), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.select_articles(items, args)[0]['id'], '2')


if __name__ == '__main__':
    unittest.main()
