"""검색 의미와 저장 연결의 회귀 테스트. 실제 브라우저 E2E와는 구분합니다."""
import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector
import search


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'cafe_id': '20179506', 'cafe_slug': 'iroid', 'timeout_seconds': 5}
        self.now = datetime(2026, 9, 11, 22, 0, tzinfo=collector.KST)
        self.snapshot = {
            'document_url': search.search_url('20179506', '불량'),
            'query_values': ['불량', '불량'], 'scope': '제목만', 'board': '전체 게시판',
            'period': '전체기간', 'sort': '최신순', 'current_page': '1', 'empty': False,
            'rows': [
                {'number': '5497665', 'title': '조립불량 유상', 'date': '16:59',
                 'href': 'https://cafe.naver.com/f-e/cafes/20179506/articles/5497665?art=TEST_ONLY'},
                {'number': '5497463', 'title': '도장불량 판별', 'date': '14:28',
                 'href': 'https://cafe.naver.com/f-e/cafes/20179506/articles/5497463'},
                {'number': '5496829', 'title': '시트 불량', 'date': '2026.09.10.',
                 'href': 'https://cafe.naver.com/f-e/cafes/20179506/articles/5496829'},
            ]}

    def test_keyword_encoded_and_cannot_change_search_parameters(self):
        word = '고장 & od=ACCURACY #?'
        url = search.search_url('20179506', word)
        self.assertEqual(parse_qs(urlsplit(url).query),
                         {'q': [word], 'ta': ['SUBJECT'], 'page': ['1'], 'od': ['LATEST']})
        for value in ['', ' ', '가'*101, '불량\n고장', None]:
            with self.subTest(value=value), self.assertRaises(collector.CollectorError):
                search.validate_keyword(value)

    def test_selects_first_result_and_preserves_link_only_in_memory(self):
        picked = search.select_latest(self.snapshot, self.cfg, '불량', self.now)
        self.assertEqual(picked.target.article_id, '5497665')
        self.assertIn('art=TEST_ONLY', picked.navigation_url)
        self.assertNotIn('TEST_ONLY', json.dumps(picked.metadata))
        self.assertNotIn('TEST_ONLY', repr(picked))
        self.assertEqual(picked.metadata['checked_list_rows'], 3)

    def test_notice_is_not_selected_as_latest(self):
        self.snapshot['rows'].insert(0, {'number': '공지', 'title': '알림', 'href': 'https://cafe.naver.com/iroid/1', 'date': '2020.01.01.'})
        self.assertEqual(search.select_latest(self.snapshot, self.cfg, '불량', self.now).target.article_id, '5497665')

    def test_stale_query_wrong_scope_and_wrong_sort_fail_closed(self):
        changes = [{'query_values': ['고장', '불량']}, {'query_values': []}, {'scope': '댓글내용'},
                   {'period': '1개월'}, {'board': '자유게시판'}, {'sort': '정확도순'},
                   {'current_page': '2'}, {'document_url': self.snapshot['document_url'].replace('LATEST', 'ACCURACY')}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(collector.CollectorError) as err:
                search.select_latest(self.snapshot | change, self.cfg, '불량', self.now)
            self.assertEqual(err.exception.code, 'SEARCH_CONDITION_MISMATCH')

    def test_bad_first_result_is_not_silently_replaced_by_second(self):
        for change in [{'href': 'https://cafe.naver.com/f-e/cafes/999/articles/5497665'},
                       {'href': 'https://cafe.naver.com/f-e/cafes/20179506/articles/123'},
                       {'href': 'https://example.invalid/5497665'}, {'title': ''}, {'number': ''}]:
            snapshot = copy.deepcopy(self.snapshot)
            snapshot['rows'][0].update(change)
            with self.subTest(change=change), self.assertRaises(collector.CollectorError):
                search.select_latest(snapshot, self.cfg, '불량', self.now)

    def test_out_of_order_dates_rejected(self):
        self.snapshot['rows'][1]['date'] = '19:00'
        with self.assertRaises(collector.CollectorError) as err:
            search.select_latest(self.snapshot, self.cfg, '불량', self.now)
        self.assertEqual(err.exception.code, 'SEARCH_ORDER_MISMATCH')

    def test_same_minute_keeps_site_order(self):
        self.snapshot['rows'][1]['date'] = '16:59'
        self.assertEqual(search.select_latest(self.snapshot, self.cfg, '불량', self.now).target.article_id, '5497665')

    def test_empty_is_explicit_and_not_loading_failure(self):
        self.assertIsNone(search.select_latest(self.snapshot | {'rows': [], 'empty': True}, self.cfg, '불량'))
        for change in [{'rows': []}, {'empty': True}]:
            with self.subTest(change=change), self.assertRaises(collector.CollectorError):
                search.select_latest(self.snapshot | change, self.cfg, '불량')

    def test_unknown_and_invalid_dates_rejected(self):
        for raw in ['방금', '2026.02.30.', '25:10', '']:
            with self.subTest(raw=raw), self.assertRaises(collector.CollectorError):
                search.list_date(raw, self.now)

    def test_observed_live_dom_selects_expected_article_and_empty_state(self):
        # 실제 DOM 추출값이며, 검색 링크의 일회성 쿼리는 제거해 보관했습니다.
        fixture = json.loads((Path(__file__).parent / 'observed_search.json').read_text())
        result = search.select_latest(fixture['found'], self.cfg, '고장', self.now)
        self.assertEqual(result.target.article_id, '5497499')
        self.assertEqual(result.metadata['selected_title'], '더뉴(tm) 클락션 고장')
        self.assertIsNone(search.select_latest(fixture['empty'], self.cfg, 'v2_no_result_7f39c0b4', self.now))

    def test_search_timeout_and_login_redirect_are_separate(self):
        from playwright.sync_api import TimeoutError
        page = MagicMock()
        page.frames = []
        page.goto.side_effect = TimeoutError('test')
        with self.assertRaises(collector.CollectorError) as err:
            search.find_latest(page, self.cfg, '불량', TimeoutError)
        self.assertEqual(err.exception.code, 'SEARCH_NOT_READY')
        page.frames = [SimpleNamespace(url='https://nid.naver.com/nidlogin.login')]
        with self.assertRaises(collector.CollectorError) as err:
            search.find_latest(page, self.cfg, '불량', TimeoutError)
        self.assertEqual(err.exception.code, 'LOGIN_REQUIRED')

    def test_search_link_must_match_selected_article_before_navigation(self):
        from playwright.sync_api import TimeoutError
        page = MagicMock()
        with self.assertRaises(collector.CollectorError) as err:
            collector.collect_article(page, collector.Target('20179506', '1'), self.cfg, TimeoutError,
                                      navigation_url='https://cafe.naver.com/iroid/2')
        self.assertEqual(err.exception.code, 'WRONG_ARTICLE')
        page.goto.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
