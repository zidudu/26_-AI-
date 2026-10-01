"""브라우저를 열지 않는 단위 테스트. 실사이트 E2E 테스트와 구분합니다."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.target = collector.Target("20179506", "4528140")
        self.raw = {"title": "테스트 제목", "date": "2026.09.11. 16:59",
                    "body": "첫 문장\n\n\u200b\n\n둘째 문장"}

    def test_supported_urls_and_query_not_persisted(self):
        urls = [self.target.url + "?art=TEST_ONLY&query=ignored",
                "https://cafe.naver.com/iroid/4528140",
                "https://cafe.naver.com/ArticleRead.nhn?clubid=20179506&articleid=4528140",
                "https://cafe.naver.com/ca-fe/cafes/20179506/articles/4528140"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(collector.parse_target(url, "20179506", "iroid"), self.target)
        self.assertNotIn("?", self.target.url)

    def test_wrong_site_or_cafe_and_non_article_rejected(self):
        for url in ["https://cafe.naver.com.evil.invalid/iroid/1", "http://cafe.naver.com/iroid/1",
                    "https://cafe.naver.com/iroid", "https://cafe.naver.com/f-e/cafes/999/articles/1",
                    "https://someone@cafe.naver.com/iroid/1", "https://cafe.naver.com:123/iroid/1"]:
            with self.subTest(url=url), self.assertRaises(collector.CollectorError):
                collector.parse_target(url, "20179506", "iroid")

    def test_raw_text_preserved_and_kst_date(self):
        article = collector.build_article(self.raw, self.target)
        self.assertEqual(article["body_raw"], self.raw["body"])
        self.assertEqual(article["body"], "첫 문장\n\n둘째 문장")
        self.assertEqual(article["written_at"], "2026-09-11T16:59:00+09:00")
        self.assertEqual(len(article["body"]), article["body_char_count"])

    def test_missing_title_empty_body_and_bad_date_rejected(self):
        for change in [{"title": " "}, {"body": "\u200b\n "}, {"body": None},
                       {"date": "2026.02.30. 12:00"}, {"date": "어제"},
                       {"body": "로딩중입니다."}]:
            with self.subTest(change=change), self.assertRaises(collector.CollectorError):
                collector.build_article(self.raw | change, self.target)

    def test_rerun_does_not_overwrite_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            article = collector.build_article(self.raw, self.target)
            first = collector.save_article(output, article)
            second = collector.save_article(output, article)
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text(encoding="utf-8")), article)
            self.assertEqual(len(list(output.glob("*.json"))), 2)
            self.assertEqual(list(output.glob("*.tmp")), [])

    def test_interrupted_save_leaves_no_partial_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(collector.os, "replace", side_effect=OSError("test only")):
                with self.assertRaises(OSError):
                    collector.save_article(output, collector.build_article(self.raw, self.target))
            self.assertEqual(list(output.iterdir()), [])

    def test_config_resolves_paths_relative_to_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            content = json.loads((Path(__file__).parent / "config_fixture.json").read_text())
            path.write_text(json.dumps(content), encoding="utf-8-sig")
            config, target = collector.read_config(path)
            self.assertEqual(config["output_dir"], Path(directory) / "output_v2")
            self.assertEqual(target, self.target)

    def test_config_profile_and_output_must_be_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            content = json.loads((Path(__file__).parent / "config_fixture.json").read_text())
            for value in ["data/browser_profile", "data/browser_profile/output", "data"]:
                content["output_dir"] = value
                path.write_text(json.dumps(content), encoding="utf-8")
                with self.assertRaises(collector.CollectorError):
                    collector.read_config(path)

    def test_wrong_article_and_login_redirect_rejected(self):
        page = SimpleNamespace(url=self.target.url, frames=[SimpleNamespace(url="https://nid.naver.com/nidlogin.login")])
        with self.assertRaises(collector.CollectorError) as ctx:
            collector.assert_target_page(page, self.target, "iroid")
        self.assertEqual(ctx.exception.code, "LOGIN_REQUIRED")
        page.frames = []
        page.url = "https://cafe.naver.com/f-e/cafes/20179506/articles/999"
        with self.assertRaises(collector.CollectorError) as ctx:
            collector.assert_target_page(page, self.target, "iroid")
        self.assertEqual(ctx.exception.code, "WRONG_ARTICLE")

    def test_browser_flow_validates_inner_frame_identity(self):
        # 모의 브라우저: 다른 글의 프레임 내용이 섞이는 위험을 검사합니다.
        from playwright.sync_api import TimeoutError
        page = MagicMock()
        page.frames = []
        page.url = self.target.url
        page.goto.return_value.status = 200
        page.frame_locator.return_value.locator.return_value.evaluate.return_value = (
            self.raw | {"document_url": "https://cafe.naver.com/ca-fe/cafes/20179506/articles/999"})
        cfg = {"timeout_seconds": 5, "cafe_slug": "iroid"}
        with patch.object(collector, "wait_for_article", return_value=self.raw | {"document_url": "https://cafe.naver.com/ca-fe/cafes/20179506/articles/999"}):
            with self.assertRaises(collector.CollectorError) as ctx:
                collector.collect_article(page, self.target, cfg, TimeoutError)
        self.assertEqual(ctx.exception.code, "WRONG_ARTICLE")

    def test_browser_timeout_does_not_return_an_article(self):
        from playwright.sync_api import TimeoutError
        page = MagicMock()
        page.frames = []
        page.goto.side_effect = TimeoutError("test only")
        with self.assertRaises(collector.CollectorError) as ctx:
            collector.collect_article(page, self.target, {"timeout_seconds": 5}, TimeoutError)
        self.assertEqual(ctx.exception.code, "PAGE_NOT_READY")

    def test_empty_body_timeout_includes_diagnostics(self):
        page = MagicMock()
        page.frames = []
        page.frame.return_value = None
        with patch.object(collector.time, 'monotonic', side_effect=[0, 6]):
            with self.assertRaises(collector.CollectorError) as ctx:
                collector.wait_for_article(page, {'timeout_seconds': 5})
        self.assertEqual(ctx.exception.code, 'PAGE_NOT_READY')
        self.assertFalse(ctx.exception.details['frame_detected'])
        page.wait_for_timeout.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
