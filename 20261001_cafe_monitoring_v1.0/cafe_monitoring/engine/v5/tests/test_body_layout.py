"""배포 JS를 Node에서 그대로 실행해 준비 검사와 추출의 연결을 검증합니다."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector
import page_guard

NODE = shutil.which('node')
FIXTURE = Path(__file__).with_name('body_fixture.cjs')


class NodeFrame:
    url = 'https://cafe.naver.com/f-e/cafes/20179506/articles/5497597'

    def __init__(self, options):
        self.options = options

    def evaluate(self, script, argument=None):
        payload = json.dumps({'options': self.options, 'script': script, 'argument': argument}, ensure_ascii=False)
        result = subprocess.run([NODE, str(FIXTURE)], input=payload, text=True, encoding='utf-8',
                                capture_output=True, check=True)
        return json.loads(result.stdout)


@unittest.skipUnless(NODE, 'Node.js가 없으면 합성 DOM 테스트를 건너뜁니다.')
class BodyLayoutTests(unittest.TestCase):
    def extract(self, options):
        frame = NodeFrame(options)
        page = MagicMock()
        page.frames = [frame]
        page.frame.return_value = frame
        raw = collector.wait_for_article(page, {'timeout_seconds': 40})
        page.wait_for_timeout.assert_not_called()
        return raw

    def test_existing_modern_body_preserves_text(self):
        raw = self.extract({'modern': [{'text': '기존 본문\n두 번째 문단'}]})
        self.assertEqual(raw['body'], '기존 본문\n두 번째 문단')
        self.assertEqual(raw['body_selector'], '.se-main-container')

    def test_legacy_body_is_ready_and_extracted(self):
        raw = self.extract({'legacy': [{'text': '구형 편집기의 본문\n다음 문단'}]})
        self.assertEqual(raw['body'], '구형 편집기의 본문\n다음 문단')
        self.assertEqual(raw['body_selector'], '.article_viewer .ContentRenderer')

    def test_modern_has_priority_when_both_exist(self):
        raw = self.extract({'modern': [{'text': '기존 본문'}], 'legacy': [{'text': '외곽 컨테이너 텍스트'}]})
        self.assertEqual(raw['body'], '기존 본문')

    def test_empty_or_hidden_modern_falls_back(self):
        for modern in [{'text': ' \n\u200b '}, {'text': '숨긴 이전 본문', 'visible': False}]:
            with self.subTest(modern=modern):
                raw = self.extract({'modern': [modern], 'legacy': [{'text': '실제로 보이는 본문'}]})
                self.assertEqual(raw['body'], '실제로 보이는 본문')

    def test_selected_index_is_used_for_extraction(self):
        raw = self.extract({'legacy': [{'text': '숨긴 이전 요소', 'visible': False}, {'text': '두 번째 본문'}]})
        self.assertEqual(raw['body'], '두 번째 본문')

    def test_outside_body_menus_and_comments_are_not_read(self):
        raw = self.extract({'legacy': [{'text': '게시글 본문만'}], 'outside': [{'text': '댓글 메뉴'}],
                            'pageText': '카페 메뉴\n게시글 본문만\n댓글 내용'})
        self.assertEqual(raw['body'], '게시글 본문만')
        state = NodeFrame({'outside': [{'text': '댓글에만 본문 형태가 있음'}]}).evaluate(page_guard.STATUS_JS)
        self.assertFalse(state['ready'])
        self.assertIsNone(state['body_selector'])

    def test_grade_and_quoted_grade_are_separate(self):
        message = '싼타페 등급이 되시면 읽기가 가능한 게시판 입니다.'
        frame = NodeFrame({'pageText': message})
        state = frame.evaluate(page_guard.STATUS_JS)
        self.assertTrue(state['grade'])
        self.assertFalse(state['ready'])
        raw = self.extract({'legacy': [{'text': message}], 'pageText': message})
        self.assertEqual(raw['body'], message)

    def test_saved_metadata_identifies_body_layout(self):
        raw = self.extract({'legacy': [{'text': '첫 문단\n\n\n두 번째 문단'}]})
        data = collector.build_article(raw, collector.Target('20179506', '5497597'))
        self.assertEqual(data['body'], '첫 문단\n\n두 번째 문단')
        self.assertEqual(data['body_selector'], '.article_viewer .ContentRenderer')
        self.assertEqual(data['collector_version'], '5.1.1')


if __name__ == '__main__':
    unittest.main()
