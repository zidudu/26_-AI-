from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from v754.core import V7Error
from v9.analysis_stage import AnalysisStageResult, prepare_analysis
from v9.artifacts import read_artifact, validate_export
from v9.dashboard_data import build_stats, DEFAULTS, axis
from v9.configuration import CATALOG
from v9.ppt_notes import add_notes, verify_notes
from v9.rebuild_saved_ppt import no_new_analysis
from v9.report_paths import monitoring_path
from v8.configuration import V8Error, write_json
from v8.pipeline import sha


class BoundaryTests(unittest.TestCase):
    def test_ppt_date_converts_utc_to_korea(self):
        before = datetime(2026, 9, 28, 14, 59, tzinfo=timezone.utc)
        after = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(monitoring_path(Path('run'), created_at=before).name, '20260928_monitoring.pptx')
        self.assertEqual(monitoring_path(Path('run'), created_at=after).name, '20260929_monitoring.pptx')
        with self.assertRaises(ValueError):
            monitoring_path(Path('run'), created_at=datetime(2026, 9, 29))

    def test_existing_and_dated_artifact_paths_both_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for filename in ('monitoring.pptx', '20260929_monitoring.pptx'):
                with self.subTest(filename=filename):
                    ppt = folder / filename
                    ppt.write_bytes(b'fake')
                    write_json(folder/'summary.json', {'pptx': str(ppt)})
                    record = {'artifact': {'ppt': filename, 'ppt_sha256': sha(ppt),
                              'summary': 'summary.json', 'summary_sha256': sha(folder/'summary.json')}}
                    summary, actual = read_artifact(folder, record)
                    self.assertEqual(actual, ppt)
                    self.assertEqual(summary['pptx'], str(ppt))

    def test_unverified_empty_dashboard_is_rejected(self):
        report = dict(status='completed_empty', selected_articles=0, items=[],
                      collection_complete=True, dashboard_stats={'total_posts': 0})
        with self.assertRaises(V8Error) as caught:
            validate_export(report, Path('.'), [])
        self.assertEqual(caught.exception.code, 'PPT_UNVERIFIED')

    def test_snapshot_keeps_original_analysis_independent_of_ppt_repairs(self):
        original = [{'cafe_id':'1', 'id':'7', 'source_artifact_sha256':'raw-sha',
                     'analysis_candidate':{'summary':['원문 근거 요약']}, 'display_text':'원래 표시'}]
        result = AnalysisStageResult(original, Mock(), Mock())
        snapshot = result.snapshot()
        original[0]['display_text'] = '배치 후 표시'
        original[0]['analysis_candidate']['summary'][0] = '다른 표현'
        self.assertEqual(snapshot[0]['analysis_candidate']['summary'], ['원문 근거 요약'])
        self.assertEqual(snapshot[0]['display_text'], '원래 표시')
        self.assertEqual(snapshot[0]['source_artifact_sha256'], 'raw-sha')

    def test_raw_notes_preserve_spaces_and_existing_review_text(self):
        target = SimpleNamespace(Text='')
        shape = SimpleNamespace(Type=14, PlaceholderFormat=SimpleNamespace(Type=2),
                                TextFrame=SimpleNamespace(TextRange=target))
        slide = SimpleNamespace(NotesPage=SimpleNamespace(Shapes=SimpleNamespace(Count=1, Item=lambda n:shape)))
        raw = '  원문 첫 줄\n\n마지막 줄  \n'
        with patch('v8.analysis_engine.load_bound_source', return_value=({'body_raw':raw}, None)), \
             patch('v9.ppt_notes.legacy.add_notes', side_effect=lambda *a: setattr(target, 'Text', '기존 검토 정보')):
            expected = add_notes(slide, {'id':'7'}, {}, '1/1', {'warnings':[]})
        self.assertEqual(expected, '기존 검토 정보\r\r[원문 본문]\r'+raw)
        target.Text = expected.replace('\r', '\n')
        verify_notes(slide, expected)
        target.Text = target.Text.rstrip()
        with self.assertRaises(V7Error):
            verify_notes(slide, expected)

    def test_saved_rebuild_never_generates_new_article_analysis(self):
        article = {'cafe_id':'1', 'id':'7'}
        with redirect_stdout(StringIO()):
            self.assertFalse(no_new_analysis(article, 'SEMANTIC_REVIEW'))
        with self.assertRaises(V7Error) as caught:
            no_new_analysis(article, 'SUMMARY_OVERFLOW')
        self.assertEqual(caught.exception.code, 'REBUILD_NEEDS_ANALYSIS')


class AnalysisSessionTests(unittest.TestCase):
    def test_provider_lifetime_includes_callbacks_and_closes_on_failure(self):
        events = []
        @contextmanager
        def binding(*args):
            events.append('open')
            try:
                yield {'complaint_definition':'fixture', 'model':'fixture'}, Mock()
            finally:
                events.append('close')

        cfg = {'project_root':Path('.'), 'v95_ai_settings':{'provider':'codex', 'codex':{'max_parallel':2}},
               'v93_performance':{'analysis_concurrency':3}}
        report = {'collection_complete':True}
        checkpoint = Mock()
        with patch('v9.ai_provider.analysis_binding', binding), \
             patch('v9.ai_provider.show_settings'), \
             patch('v9.analysis_engine.analyze_articles', return_value=[{'id':'7'}]) as analyze, \
             patch('v9.comparison.write_comparison'), \
             patch('v9.summary_repair.SummaryRepair') as repair, \
             patch('v9.dashboard_ai.prepare', side_effect=lambda *a, **kw: events.append('dashboard')), \
             redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'consumer failure'):
                with prepare_analysis([{'id':'7'}], cfg, Path('.'), report, checkpoint) as session:
                    self.assertEqual(events, ['open'])
                    self.assertIs(session.summary_resolver, repair.return_value)
                    session.dashboard_resolver(session.articles)
                    self.assertEqual(analyze.call_args.kwargs['concurrency'], 2)
                    raise RuntimeError('consumer failure')
        self.assertEqual(events, ['open', 'dashboard', 'close'])

    def test_incomplete_collection_is_rejected_before_provider_open(self):
        with patch('v9.ai_provider.analysis_binding') as provider:
            with self.assertRaises(V7Error):
                with prepare_analysis([], {}, Path('.'), {}, Mock()):
                    pass
        provider.assert_not_called()


class DashboardDataTests(unittest.TestCase):
    def fixture(self):
        cafes = []
        items = []
        for index, (code, name, slug, cid) in enumerate(CATALOG):
            count = None if index == 7 else 0 if index == 4 else 1
            cid = str(cid or 80000000+index)
            cafes.append(dict(code=code, name=name, slug=slug, club_id=cid,
                              status='failed' if count is None else 'collected', count=count))
            if count:
                items.append({'cafe_id':cid, 'id':'1', 'matched_keywords':['SCC','경고등','SCC']})
        return items, cafes

    def test_fixed_order_zero_incomplete_and_keyword_dedup(self):
        items, cafes = self.fixture()
        result = build_stats(items, list(reversed(cafes)), ['SCC', '경고등'], deepcopy(DEFAULTS))
        self.assertEqual([c['slug'] for c in result['cafes']], [c[2] for c in CATALOG])
        self.assertEqual(result['cafes'][4]['count'], 0)
        self.assertIsNone(result['cafes'][7]['count'])
        self.assertEqual(result['cafes'][7]['keyword_counts'], [None, None])
        self.assertEqual(result['keyword_counts'], [7,7])
        self.assertEqual(result['total_posts'], 7)

    def test_large_axis_keeps_integer_unit(self):
        for values, maximum in [([0, None], 1), ([13],13), ([33],33), ([105],105)]:
            self.assertEqual(axis(values), {'minimum':0, 'maximum':maximum, 'major_unit':1})

    def test_incomplete_cafe_cannot_leak_into_confirmed_count(self):
        items, cafes = self.fixture()
        cafes[0]['status'] = 'failed'
        with self.assertRaises(V7Error) as caught:
            build_stats(items, cafes, ['SCC','경고등'], deepcopy(DEFAULTS))
        self.assertEqual(caught.exception.code, 'DASHBOARD_COLLECTION_STATE')
