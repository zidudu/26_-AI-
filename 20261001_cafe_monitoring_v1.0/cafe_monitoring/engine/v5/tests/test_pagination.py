"""V3 동작 검증. 실사이트 호출 없이 실제 목록 fixture와 실패 주입을 사용합니다."""
import copy
from contextlib import redirect_stdout, redirect_stderr
from functools import partial
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_search
import collector
from search import select_page


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = {'cafe_id': '20179506', 'cafe_slug': 'iroid', 'timeout_seconds': 5,
                    'request_interval_seconds': 1, 'max_pages': 20, 'output_dir': Path(self.temp.name)}
        self.pages = json.loads((Path(__file__).parent / 'observed_v3_pages.json').read_text())['pages']
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stderr = redirect_stderr(self.output)
        self.stdout.__enter__(); self.stderr.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def finder(self, pages=None):
        pages = pages if pages is not None else self.pages
        loader = MagicMock(side_effect=copy.deepcopy(pages))
        return partial(batch_search.find_many, loader=loader, pause=lambda _: None), loader

    def find(self, count, pages=None):
        finder, loader = self.finder(pages)
        events = []
        selected, reason = finder(None, self.cfg, '불량', count, TimeoutError,
                                  lambda selected, info: events.append(info))
        return selected, reason, loader, events

    def test_5_selects_only_first_five_on_page_one(self):
        picks, reason, loader, _ = self.find(5)
        self.assertEqual([s.target.article_id for s in picks],
                         ['5497665', '5497463', '5497069', '5496829', '5492956'])
        self.assertEqual(reason, 'REQUEST_REACHED')
        self.assertEqual(loader.call_count, 1)

    def test_20_crosses_page_boundary_and_preserves_order(self):
        picks, reason, loader, events = self.find(20)
        self.assertEqual(len(picks), 20)
        self.assertEqual(picks[15].target.article_id, '5464781')
        self.assertEqual(picks[-1].target.article_id, '5462333')
        self.assertEqual([s.metadata['result_rank'] for s in picks], list(range(1,21)))
        self.assertEqual(loader.call_count, 2)

    def test_duplicate_between_pages_is_selected_once(self):
        self.pages[1]['rows'].insert(0, copy.deepcopy(self.pages[0]['rows'][-1]))
        picks, _, _, events = self.find(20)
        self.assertEqual(len({s.target.article_id for s in picks}), 20)
        self.assertEqual(picks[-1].target.article_id, '5462333')
        self.assertEqual(events[-1]['duplicates_skipped'], 1)

    def test_same_page_repeated_is_error(self):
        self.pages[1]['rows'] = copy.deepcopy(self.pages[0]['rows'])
        with self.assertRaises(collector.CollectorError) as err:
            self.find(20)
        self.assertEqual(err.exception.code, 'PAGINATION_STALLED')

    def test_cross_page_order_change_is_error(self):
        self.pages[1]['rows'][0]['date'] = '23:59'
        with self.assertRaises(collector.CollectorError) as err:
            self.find(20)
        self.assertEqual(err.exception.code, 'SEARCH_ORDER_MISMATCH')


if __name__ == '__main__':
    unittest.main()
