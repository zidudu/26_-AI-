"""합성 관측 및 사용자 제공 HTML 구조에 근거한 오프라인 회귀 검사."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v754.collect import metadata as md
from v754.core import DEFAULTS, V7Error, write_json
from v754.powerpoint import verify_metadata_table
from v754.refresh import build_refreshed_item, count_display, refresh_articles
from .test_export import fixture, seal


def observation(comment=4, views=80, cafe='시험 카페', status='observed'):
    return {'observed_at': '2026-09-16T00:30:00+00:00',
            'comment_count': {'value': comment, 'status': status, 'raw': str(comment) if comment is not None else None,
                              'selector': '.article_header .button_comment .num'},
            'view_count': {'value': views, 'status': 'observed', 'raw': str(views), 'selector': '.article_info .count'},
            'cafe_name': {'value': cafe, 'status': 'observed' if cafe else 'missing', 'scope': 'outer_page'}}


class ClockPage:
    def __init__(self): self.time = 0
    def clock(self): return self.time
    def wait_for_timeout(self, ms): self.time += ms / 1000


class MetadataTests(unittest.TestCase):
    def settle(self, observed):
        page = ClockPage()
        article = {'metadata': {'comment_count': 0}}
        with patch.object(md, 'observe', side_effect=lambda p: observed(p.time)):
            md.initialize_metadata(page, article)
            md.settle_metadata(page, article, clock=page.clock)
        return article

    def test_delayed_zero_to_four_records_initial_and_selects_four(self):
        a = self.settle(lambda t: observation(0 if t < 1.25 else 4))
        self.assertEqual(a['metadata']['comment_count'], 4)
        self.assertEqual(a['metadata_audit']['observations'][0]['comment_count']['value'], 0)
        self.assertEqual(a['metadata_audit']['before_capture']['comment_count']['value'], 4)
        self.assertGreaterEqual(a['metadata_audit']['wait_seconds'], 2.25)

    def test_confirmed_zero_is_not_missing(self):
        a = self.settle(lambda t: observation(0))
        self.assertEqual(count_display(a['metadata'], 'comment_count'), '0')

    def test_missing_never_becomes_zero_or_previous_value(self):
        a = self.settle(lambda t: observation(None, status='missing'))
        self.assertIsNone(a['metadata']['comment_count'])
        self.assertEqual(count_display(a['metadata'], 'comment_count'), '미확인')
        self.assertEqual(a['metadata']['view_count'], 80)
        self.assertLessEqual(a['metadata_audit']['wait_seconds'], 6)

    def test_continuously_changing_count_is_unknown_but_view_count_survives(self):
        a = self.settle(lambda t: observation(int(t * 4) % 2))
        self.assertEqual(a['metadata']['status']['comment_count'], 'unstable')
        self.assertIsNone(a['metadata']['comment_count'])
        self.assertEqual(a['metadata']['view_count'], 80)

    def test_conflicting_elements_do_not_pick_a_convenient_number(self):
        a = self.settle(lambda t: observation(None, status='conflict'))
        self.assertEqual(a['metadata']['status']['comment_count'], 'conflict')
        self.assertIsNone(a['metadata']['comment_count'])

    def test_change_during_capture_is_flagged_and_not_displayed_as_matching(self):
        a = self.settle(lambda t: observation(4))
        with patch.object(md, 'observe', return_value=observation(5)):
            md.check_after_capture(None, a)
        self.assertIsNone(a['metadata']['comment_count'])
        self.assertEqual(a['metadata_audit']['before_capture']['comment_count']['value'], 4)
        self.assertEqual(a['metadata_audit']['after_capture']['comment_count']['value'], 5)
        self.assertIn('COMMENT_COUNT_CHANGED_DURING_CAPTURE', a['metadata_audit']['warnings'])

    def test_unchanged_snapshot_remains_usable(self):
        a = self.settle(lambda t: observation(4))
        with patch.object(md, 'observe', return_value=observation(4)):
            md.check_after_capture(None, a)
        self.assertEqual(count_display(a['metadata'], 'comment_count'), '4')


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.draft = fixture(self.root)
        self.path = self.root / 'draft.json';write_json(self.path, self.draft)
        self.original = {'id': '101', 'cafe_id': '20179506', 'views': '78', 'comments': '0',
            'draft_path': str(self.path), 'draft_sha256': self.draft['artifact_sha256'],
            'display_text': self.draft['monitoring']['display_text'], 'review_notes': [],
            'capture_notes': ['과거 파일 없음'], 'captures': [{'path': 'old.png'}]}
        self.new = {**deepcopy(self.draft['source']), 'collected_at': '2026-09-16T09:30:00+09:00',
            'body_sha256': hashlib.sha256(self.draft['source']['body'].encode()).hexdigest(),
            'metadata': {'cafe_name': '실제 시험 카페', 'cafe_name_source': 'outer_page',
                'view_count': 80, 'comment_count': 4, 'observed_at': '2026-09-16T00:30:00Z',
                'status': {'view_count': 'stable_observed', 'comment_count': 'stable_observed'}},
            'metadata_audit': {'warnings': [], 'observations': [observation(0)], 'selected': {}},
            'capture': {'status': 'captured', 'files': []}}
        self.cfg = dict(DEFAULTS)

    def test_existing_summary_reused_new_counts_selected_original_file_unchanged(self):
        before = self.path.read_bytes()
        item = build_refreshed_item(self.original, self.new, self.root / 'source.json', self.cfg)
        self.assertEqual(item['comments'], '4');self.assertEqual(item['views'], '80')
        self.assertEqual(item['display_text'], self.original['display_text'])
        self.assertEqual(item['cafe_name'], '실제 시험 카페')
        self.assertEqual(item['captures'], [])  # Never fall back to an old screenshot.
        self.assertEqual(self.path.read_bytes(), before)

    def test_body_changed_rejects_stale_summary(self):
        self.new['body'] += ' 작성자 수정'
        with self.assertRaises(V7Error) as c:
            build_refreshed_item(self.original, self.new, self.root / 'source.json', self.cfg)
        self.assertEqual(c.exception.code, 'SOURCE_CHANGED')

    def test_different_article_is_rejected_even_if_text_matches(self):
        self.new['article_id'] = '999'
        with self.assertRaises(V7Error) as c:
            build_refreshed_item(self.original, self.new, self.root / 'source.json', self.cfg)
        self.assertEqual(c.exception.code, 'WRONG_ARTICLE')

    def test_missing_counts_and_cafe_are_not_old_zero_or_id_placeholder(self):
        self.new['metadata'] = {'comment_count': None, 'view_count': None}
        item = build_refreshed_item(self.original, self.new, self.root / 'source.json', self.cfg)
        self.assertEqual((item['comments'], item['views'], item['cafe_name']), ('미확인', '미확인', '미확인'))

    def test_explicit_cafe_mapping_has_distinct_provenance(self):
        self.new['metadata']['cafe_name'] = None
        self.cfg['cafe_names'] = {'20179506': '직원이 확인한 카페 이름'}
        item = build_refreshed_item(self.original, self.new, self.root / 'source.json', self.cfg)
        self.assertEqual(item['cafe_name'], '직원이 확인한 카페 이름')
        self.assertEqual(item['metadata']['cafe_name_source'], 'user_config')

    def test_run_saves_source_trace_and_reusable_item_without_ai(self):
        folder = self.root / 'run';folder.mkdir()
        report = {};page = ClockPage()
        with patch('v754.refresh.collect_article', return_value=self.new):
            items = refresh_articles([self.original], self.cfg, folder, report, lambda: None,
                page=page, timeout_error=TimeoutError,
                webcfg={'cafe_id': '20179506', 'request_interval_seconds': 2})
        self.assertEqual(len(items), 1)
        audit = json.loads((folder / 'metadata_audit.json').read_text())
        self.assertEqual(audit['api_calls'], 0)
        self.assertEqual(audit['items'][0]['saved_v6']['comments'], '0')
        self.assertEqual(audit['items'][0]['ppt_metadata']['comment_count'], 4)
        source = json.loads(Path(items[0]['refreshed_source_file']).read_text())
        self.assertEqual(source['metadata']['comment_count'], 4)
        self.assertEqual(report['unprocessed_articles'], 0)

    def test_login_error_stops_and_records_remaining_posts(self):
        from v754.collect.collector import CollectorError
        folder = self.root / 'run';folder.mkdir();report = {}
        with patch('v754.refresh.collect_article', side_effect=CollectorError('LOGIN_REQUIRED', '로그인 필요')):
            items = refresh_articles([self.original, self.original], self.cfg, folder, report, lambda: None,
                page=ClockPage(), timeout_error=TimeoutError,
                webcfg={'cafe_id': '20179506', 'request_interval_seconds': 2})
        self.assertEqual(items, [])
        self.assertEqual(report['unprocessed_articles'], 1)
        self.assertEqual(report['stop_code'], 'LOGIN_REQUIRED')

    def test_ppt_readback_catches_wrong_count(self):
        cells = {(2, 2): '시험 카페', (3, 7): '80/4'}
        table = SimpleNamespace(Cell=lambda r,c: SimpleNamespace(Shape=SimpleNamespace(
            TextFrame=SimpleNamespace(TextRange=SimpleNamespace(Text=cells[r,c])))))
        a = {'id':'101', 'cafe_name':'시험 카페','views':'80','comments':'4'}
        verify_metadata_table(table,a)
        cells[3,7]='80/0'
        with self.assertRaises(V7Error):verify_metadata_table(table,a)


NODE = shutil.which('node')
@unittest.skipUnless(NODE, 'Node.js 미설치: JS 합성 DOM 검사는 개발 환경에서 수행합니다.')
class JavaScriptSelectorTests(unittest.TestCase):
    def evaluate(self, payload, script=md.READ_COUNTS):
        # 숫자 파서와 선택자 우선순위는 배포 JS를 그대로 실행합니다.
        harness = Path(__file__).with_name('metadata_fixture.cjs')
        p = subprocess.run([NODE, str(harness)], input=json.dumps({'payload':payload,'script':script}),
                           text=True, encoding='utf-8', capture_output=True, check=True)
        return json.loads(p.stdout)

    def test_user_html_top_comment_four_beats_bottom_placeholder(self):
        actual = self.evaluate({'top':['4'], 'bottom':['0'], 'views':['조회 80']})
        self.assertEqual(actual['comment_count']['value'],4)
        self.assertEqual(actual['view_count']['value'],80)
        self.assertIn('article_header',actual['comment_count']['selector'])

    def test_hidden_first_element_does_not_hide_visible_count(self):
        actual=self.evaluate({'top':[{'text':'0','visible':False},'8'],'views':['조회 1,234']})
        self.assertEqual(actual['comment_count']['value'],8)
        self.assertEqual(actual['view_count']['value'],1234)

    def test_missing_invalid_conflict_and_real_zero(self):
        for top, status in (([], 'missing'), (['로딩 중'], 'invalid'), (['4','0'], 'conflict'), (['0'],'observed')):
            actual=self.evaluate({'top':top,'views':['조회 80']})['comment_count']
            self.assertEqual(actual['status'],status)
            self.assertEqual(actual['value'],0 if status=='observed' else None)

    def test_cafe_selector_requires_explicit_name_not_banner_guess(self):
        actual=self.evaluate({'cafe':['실제 카페 이름']},md.READ_CAFE)
        self.assertEqual(actual['value'],'실제 카페 이름')
        self.assertIsNone(self.evaluate({'cafe':['네이버 카페']},md.READ_CAFE)['value'])
        self.assertIsNone(self.evaluate({'cafe':[]},md.READ_CAFE)['value'])

if __name__ == '__main__':unittest.main()
