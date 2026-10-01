"""기간 경계·여러 페이지·중복·검색 변경·읽기 실패. 실제 네이버 통신은 하지 않습니다."""
from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from v754.period import Window, input_time
from v754.period_scan import scan_range
from v754.collect.search import search_url
from v754.collect.collector import KST, CollectorError, collect_article, Target
from v754.core import V7Error

NOW = datetime(2026, 9, 17, 12, tzinfo=KST)
WEB = {'cafe_id': '20179506', 'cafe_slug': 'iroid'}


def window():
    return Window.from_input('2026-09-16 09:00', '2026-09-17 09:00', NOW)


def snapshot(word, page, rows, more=False, empty=False):
    return {'document_url': search_url(WEB['cafe_id'], word, page), 'query_values': [word],
            'scope': '제목만', 'period': '전체기간', 'board': '전체 게시판', 'sort': '최신순',
            'current_page': str(page), 'pagination_present': True,
            'page_numbers': [page, page+1] if more else [page], 'next_group': False, 'empty': empty,
            'rows': [{'number': aid, 'title': '센서 질문 ' + aid,
                      'href': f"https://cafe.naver.com/f-e/cafes/20179506/articles/{aid}", 'date': day}
                     for aid, day in rows]}


class WindowTests(unittest.TestCase):
    def test_minute_edges_and_adjacent_period(self):
        w = window()
        for text, expected in [('2026-09-16T08:59:59+09:00', False),
                               ('2026-09-16T09:00:00+09:00', True),
                               ('2026-09-17T08:59:59+09:00', True),
                               ('2026-09-17T09:00:00+09:00', False)]:
            with self.subTest(text=text):
                self.assertEqual(w.contains(text), expected)
        next_window = Window(input_time('2026-09-17 09:00'), input_time('2026-09-18 09:00'))
        self.assertTrue(next_window.contains('2026-09-17T09:00:00+09:00'))

    def test_utc_and_kst_same_instant(self):
        self.assertTrue(window().contains('2026-09-16T00:00:00+00:00'))
        self.assertEqual(Window.from_record(window().record()), window())

    def test_start_day_is_not_midnight_exclusion(self):
        self.assertEqual(window().day_relation(input_time('2026-09-16 00:00').date()), 'candidate')
        self.assertEqual(window().day_relation(input_time('2026-09-15 00:00').date()), 'older')
        self.assertEqual(window().day_relation(input_time('2026-09-18 00:00').date()), 'newer')

    def test_midnight_end_and_leap_day(self):
        w = Window.from_input('2024-02-29 00:00', '2024-03-01 00:00', NOW)
        self.assertEqual(w.day_relation(input_time('2024-03-01 00:00').date()), 'newer')
        self.assertTrue(w.contains('2024-02-29T23:59:00+09:00'))

    def test_invalid_or_future_window(self):
        for start, end in [('2026-09-17', '2026-09-18'), ('2026-09-16 09:00', '2026-09-16 09:00'),
                           ('2026-09-17 09:00', '2026-09-16 09:00'), ('2026-02-30 09:00', '2026-03-01 09:00'),
                           ('2026-09-16 09:00', '2026-09-18 09:00'), ('2026-09-16 09:00:30', '2026-09-17 09:00')]:
            with self.subTest(start=start, end=end), self.assertRaises(V7Error):
                Window.from_input(start, end, NOW)
        with self.assertRaises(V7Error):
            window().contains('2026-09-16T09:00')


class ScanTests(unittest.TestCase):
    def run_scan(self, pages, times, words=('센서',), cap=10, override=None, now_fn=lambda: NOW):
        self.audit, self.fetches, self.loads = {}, [], []
        def loader(word, page):
            self.loads.append((word, page))
            if override:
                altered = override(word, page, self.loads.count((word, page)))
                if altered is not None:
                    return altered
            result = pages[(word, page)]
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)
        def fetch(choice):
            aid = choice.target.article_id
            self.fetches.append(aid)
            value = times[aid]
            if isinstance(value, Exception):
                raise value
            return {'article_id': aid, 'cafe_id': WEB['cafe_id'], 'written_at': value}
        def save(source):
            return {'id': source['article_id'], 'written_at': source['written_at'], 'comments': '4',
                    'captures': [], 'refreshed_source_file': '/fixture/not-a-real-source.json'}
        with redirect_stdout(io.StringIO()):
            return scan_range(window(), list(words), WEB, {'max_pages_per_keyword': cap}, self.audit,
                              loader=loader, fetch=fetch, save_item=save, checkpoint=lambda: None, now_fn=now_fn)

    def test_all_pages_dynamic_count_exact_edges_and_duplicate(self):
        pages = {('센서', 1): snapshot('센서', 1, [('14','10:00'), ('13','08:59'), ('12','2026.09.16.')], True),
                 ('센서', 2): snapshot('센서', 2, [('12','2026.09.16.'), ('11','2026.09.16.'), ('10','2026.09.16.'), ('9','2026.09.15.')])}
        times = {'14':'2026-09-17T10:00:00+09:00', '13':'2026-09-17T08:59:00+09:00',
                 '12':'2026-09-16T15:00:00+09:00', '11':'2026-09-16T09:00:00+09:00', '10':'2026-09-16T08:59:00+09:00'}
        items = self.run_scan(pages, times)
        self.assertEqual([i['id'] for i in items], ['13','12','11'])
        self.assertEqual(self.fetches.count('12'), 1)
        self.assertNotIn('9', self.fetches)
        self.assertTrue(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['rechecked_pages'], 2)
        self.assertEqual([r['status'] for r in self.audit['articles']].count('outside_period'), 2)

    def test_multiple_keywords_union_once(self):
        pages = {(k,1): snapshot(k,1,[('1','2026.09.16.')]) for k in ('센서','고장')}
        result = self.run_scan(pages, {'1':'2026-09-16T10:00:00+09:00'}, ('센서','고장'))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['matched_keywords'], ['센서','고장'])
        self.assertEqual(self.fetches, ['1'])

    def test_more_than_old_fixed_count(self):
        rows=[(str(n),'2026.09.16.') for n in range(40,0,-1)]
        pages={('센서',n+1):snapshot('센서',n+1,rows[n*10:(n+1)*10],n<3) for n in range(4)}
        result=self.run_scan(pages,{str(n):'2026-09-16T12:00:00+09:00' for n in range(1,41)})
        self.assertEqual(len(result),40)
        self.assertTrue(self.audit['complete'])

    def test_no_results_is_success_without_article_fetch(self):
        result=self.run_scan({('센서',1):snapshot('센서',1,[],empty=True)}, {})
        self.assertEqual(result, [])
        self.assertEqual(self.fetches, [])
        self.assertTrue(self.audit['complete'])

    def test_page_limit_is_not_success(self):
        self.run_scan({('센서',1):snapshot('센서',1,[('1','2026.09.16.')],True)},
                      {'1':'2026-09-16T12:00:00+09:00'},cap=1)
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'PAGE_LIMIT')

    def test_article_failure_is_not_outside_or_zero_success(self):
        self.run_scan({('센서',1):snapshot('센서',1,[('1','2026.09.16.')])},
                      {'1':CollectorError('PAGE_NOT_READY','fixture')})
        self.assertTrue(self.audit['range_search_complete'])
        self.assertFalse(self.audit['articles_verified_complete'])
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['articles'][0]['status'],'unverified')

    def test_article_date_disagrees_with_list(self):
        self.run_scan({('센서',1):snapshot('센서',1,[('1','2026.09.16.')])},
                      {'1':'2026-09-15T12:00:00+09:00'})
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['articles'][0]['code'],'LIST_ARTICLE_DATE_MISMATCH')

    def test_search_change_during_capture(self):
        def override(word,page,n):
            return snapshot(word,page,[('2','2026.09.16.')]) if n==2 else None
        self.run_scan({('센서',1):snapshot('센서',1,[('1','2026.09.16.')])},
                      {'1':'2026-09-16T12:00:00+09:00'},override=override)
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'SEARCH_CHANGED_DURING_SCAN')

    def test_repeated_page(self):
        pages={('센서',n):snapshot('센서',n,[('1','2026.09.16.')],True) for n in (1,2)}
        self.run_scan(pages, {'1':'2026-09-16T12:00:00+09:00'})
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'PAGINATION_STALLED')

    def test_unknown_pagination_is_incomplete(self):
        s=snapshot('센서',1,[('1','2026.09.16.')]);s['pagination_present']=False
        self.run_scan({('센서',1):s}, {'1':'2026-09-16T12:00:00+09:00'})
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'PAGINATION_UNKNOWN')

    def test_wrong_search_condition_is_incomplete(self):
        s=snapshot('센서',1,[('1','2026.09.16.')]);s['sort']='정확도순'
        self.run_scan({('센서',1):s},{})
        self.assertEqual(self.fetches,[])
        self.assertFalse(self.audit['complete'])

    def test_auth_failure_stops_remaining_keywords(self):
        self.run_scan({('센서',1):CollectorError('LOGIN_REQUIRED','fixture')},{},('센서','고장'))
        self.assertEqual(self.loads,[('센서',1)])
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][1]['status'],'not_started')

    def test_midnight_during_read_not_assumed_today(self):
        values=iter([datetime(2026,9,16,23,59,59,tzinfo=KST), datetime(2026,9,17,0,0,1,tzinfo=KST)])
        self.run_scan({('센서',1):snapshot('센서',1,[('1','23:59')])},{},now_fn=lambda:next(values))
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'SEARCH_DATE_ROLLOVER')

    def test_across_page_order_change(self):
        pages={('센서',1):snapshot('센서',1,[('1','2026.09.16.')],True),
               ('센서',2):snapshot('센서',2,[('2','08:00')])}
        self.run_scan(pages,{'1':'2026-09-16T12:00:00+09:00'})
        self.assertFalse(self.audit['complete'])
        self.assertEqual(self.audit['keyword_results'][0]['reason'],'SEARCH_ORDER_MISMATCH')


class CollectorGateTests(unittest.TestCase):
    def test_outside_period_skips_capture_and_metadata(self):
        page=MagicMock()
        page.goto.return_value=SimpleNamespace(status=200)
        raw={'title':'fixture','date':'2026.09.16. 08:59','body':'본문', 'media':{}}
        cfg={**WEB,'timeout_seconds':40,'capture_output_dir':'unused','period_window':window().record()}
        with patch('v754.collect.collector.assert_article_access'), patch('v754.collect.collector.assert_target_page'), \
             patch('v754.collect.collector.has_login_redirect',return_value=False), \
             patch('v754.collect.collector.wait_for_article',return_value=raw), \
             patch('v754.collect.metadata.initialize_metadata') as meta, \
             patch('v754.collect.post_capture.capture_post') as capture:
            result=collect_article(page,Target('20179506','1'),cfg,TimeoutError)
        meta.assert_not_called(); capture.assert_not_called()
        self.assertEqual(result['capture']['reason'],'outside_period')


if __name__ == '__main__':
    unittest.main()
