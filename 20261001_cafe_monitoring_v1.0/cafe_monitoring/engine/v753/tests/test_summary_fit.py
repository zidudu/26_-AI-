"""요약 분할 회귀 검사. 회사 PC의 실제 PowerPoint 글꼴 측정은 별도 확인합니다."""
import copy
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v753.core import DEFAULTS, V7Error, wrap_text
from v753.powerpoint import (ROWS, format_text, plan_article, plan_article_for_office,
                            text_bounds_fit, verify_summary_fit)
from v753.tests.test_v753_layout import article
from v753.text_layout import paginate_text, split_for_box


# 사용자가 제공한 2026-09-17 EGR PPT 화면에서 옮긴 요약입니다. API 원본 응답 파일은 아닙니다.
EGR_SCREEN_TEXT = (
    '작성자는 고속도로 주행 중 엔진 경고등이 들어왔고, 블루핸즈 점검 결과 EGR 센서 오작동과 '
    '고장코드 P040400이라고 전했습니다. 작성자는 출고 1년도 안 된 점을 언급하며 불만을 '
    '나타냈습니다. 작성자는 해당 정비에 걸리는 시간을 문의했고, 진단업체에서 3시간 걸린다고 '
    '한 말이 맞는지 질문했습니다. 작성자는 근처 블루핸즈 예약이 되지 않아 멀리 가서 예약했다고 전했습니다.'
)


class SummaryPlanTests(unittest.TestCase):
    def test_reported_egr_is_four_old_lines_but_now_one_slide_estimate(self):
        old_lines = wrap_text(EGR_SCREEN_TEXT, 53)
        self.assertEqual(len(old_lines), 4)
        self.assertTrue(old_lines[2].endswith('블루핸'))
        self.assertTrue(old_lines[3].startswith('즈'))
        a = article([(800, 340)], '5482932')
        a['display_text'] = EGR_SCREEN_TEXT
        before = copy.deepcopy(a)
        pages = plan_article(a, DEFAULTS)
        self.assertEqual(a, before)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]['summary'], EGR_SCREEN_TEXT)
        self.assertEqual(len(pages[0]['tiles']), 1)
        self.assertFalse(pages[0]['summary_continues'])

    def test_injected_measured_fit_uses_entire_text_in_every_capture_page(self):
        a = article([(800, 1200)] * 8)
        a['display_text'] = EGR_SCREEN_TEXT
        calls = []
        def fits(kind, value):
            calls.append((kind, value))
            return True
        pages = plan_article(a, DEFAULTS, text_fits=fits)
        self.assertEqual(len(pages), 4)
        self.assertEqual(calls, [('summary', EGR_SCREEN_TEXT)])
        self.assertTrue(all(p['summary'] == EGR_SCREEN_TEXT for p in pages))
        self.assertEqual([t['capture']['index'] for p in pages for t in p['tiles']], list(range(1, 9)))

    def test_genuinely_long_source_preserves_all_characters_and_capture_order(self):
        a = article([(800, 1200)] * 3)
        a['source_mode'] = True
        a['display_text'] = ('블루핸즈 예약이 되지 않아 멀리 가서 예약했습니다.\n' * 30)
        pages = plan_article(a, DEFAULTS, text_fits=lambda kind, s: len(s) <= (70 if kind == 'summary' else 150))
        restored = pages[0]['summary'] + ''.join(p['text'] for p in pages if p['kind'] == 'text')
        self.assertEqual(restored, a['display_text'])
        self.assertGreater(len(pages), 2)
        self.assertTrue(pages[0]['summary_continues'])
        self.assertEqual(pages[1]['kind'], 'text')
        self.assertEqual([t['capture']['index'] for p in pages for t in p['tiles']], [1, 2, 3])

    def test_fallback_breaks_at_sentence_then_space_not_inside_bluehands(self):
        text = '작성자는 정비를 문의했습니다. 작성자는 근처 블루핸즈 예약을 완료했습니다.'
        first, rest = split_for_box(text, lambda s: len(s) <= 32)
        self.assertEqual(first, '작성자는 정비를 문의했습니다. ')
        self.assertEqual(first + rest, text)
        text = '작성자는 근처 블루핸즈 예약을 문의'
        first, rest = split_for_box(text, lambda s: len(s) <= text.index('즈'))
        self.assertTrue(rest.startswith('블루핸즈'))
        self.assertEqual(first + rest, text)

    def test_long_token_and_combining_text_are_not_dropped(self):
        text = '가' * 300 + 'e\u0301' * 30
        pieces = paginate_text(text, lambda s: len(s) <= 17)
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(not p.startswith('\u0301') for p in pieces))

    def test_no_capacity_fails_instead_of_looping_or_dropping_text(self):
        with self.assertRaises(V7Error):
            split_for_box('표시할 내용', lambda s: False)


class FakeRange:
    def __init__(self):
        self.Text = ''
        self.Font = SimpleNamespace(Size=12, Color=SimpleNamespace())
        self.ParagraphFormat = SimpleNamespace()

    @property
    def BoundWidth(self):
        return 690 if self.Text else 0

    @property
    def BoundHeight(self):
        # 글꼴을 줄일 때만 맞는 경우를 모사합니다. Windows의 실제 글꼴 측정식이 아닙니다.
        if not self.Text:
            return 0
        return math.ceil(len(self.Text) / 60) * self.Font.Size * 1.3


def fake_shape():
    return SimpleNamespace(Width=721, Height=68, TextFrame=SimpleNamespace(
        TextRange=FakeRange(), MarginLeft=5, MarginRight=5, MarginTop=3, MarginBottom=3))


class BoundsTests(unittest.TestCase):
    def test_full_text_shrinks_to_fit_without_content_loss(self):
        shape = fake_shape()
        tr = format_text(shape, EGR_SCREEN_TEXT, '맑은 고딕', 12, min_size=9, table_cell=True)
        self.assertEqual(tr.Text, EGR_SCREEN_TEXT)
        self.assertLess(tr.Font.Size, 12)
        self.assertGreaterEqual(tr.Font.Size, 9)
        self.assertTrue(text_bounds_fit(shape))

    def test_reopen_guard_detects_overflow_and_changed_summary_row(self):
        shape = fake_shape()
        row = SimpleNamespace(Height=ROWS[3])
        table = SimpleNamespace(Cell=lambda r, c: SimpleNamespace(Shape=shape),
                                Rows=SimpleNamespace(Item=lambda r: row))
        format_text(shape, EGR_SCREEN_TEXT, '맑은 고딕', 12, min_size=9, table_cell=True)
        verify_summary_fit(table)
        shape.TextFrame.TextRange.Font.Size = 30
        with self.assertRaises(V7Error):
            verify_summary_fit(table)
        shape.TextFrame.TextRange.Text = ''
        row.Height = 100
        with self.assertRaises(V7Error):
            verify_summary_fit(table)

    def test_office_planner_measures_full_text_and_removes_probe(self):
        shape = fake_shape()
        row = SimpleNamespace(Height=ROWS[3])
        table = SimpleNamespace(Cell=lambda r, c: SimpleNamespace(Shape=shape),
                                Rows=SimpleNamespace(Item=lambda r: row))
        slides = SimpleNamespace(Count=2)
        deleted = []
        def delete():
            slides.Count -= 1
            deleted.append(True)
        slides.Item = lambda i: SimpleNamespace(Delete=delete)
        deck = SimpleNamespace(Slides=slides)
        probe = SimpleNamespace(Shapes=SimpleNamespace(
            Item=lambda name: SimpleNamespace(Table=table), AddTextbox=lambda *args: fake_shape()))
        def make(*args):
            slides.Count += 1
            return probe
        a = article([(800, 340)], '5482932')
        a['display_text'] = EGR_SCREEN_TEXT
        with patch('v753.powerpoint.make_frame', side_effect=make):
            pages = plan_article_for_office(deck, a, {'font_name': '맑은 고딕', **DEFAULTS})
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]['summary'], EGR_SCREEN_TEXT)
        self.assertEqual(pages[0]['text_measurement'], 'powerpoint_com')
        self.assertEqual(slides.Count, 2)
        self.assertEqual(deleted, [True])


if __name__ == '__main__':
    unittest.main()
