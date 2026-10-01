"""실제 v754의 저장·분석·캐시·종료 상태와 V8을 연결합니다. 외부 경계만 모사합니다."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from io import StringIO
import unittest
from unittest.mock import patch

from v8.backend import Backend
from v8.configuration import DEFAULT, parse_time, read_json
from v8.pipeline import Runner
from v754.tests import test_period_pipeline as fixtures


class BackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PeriodPipelineTests("test_collect_saved_export_and_rebuild_do_not_need_v6_drafts")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cfg = deepcopy(DEFAULT)
        self.cfg["schedule"]["initial_start"] = "2026-09-16 09:00"
        self.cfg["mail"]["to"] = ["test@example.invalid"]
        self.calls = []

    def render(self, items, cfg, folder, report, checkpoint, **kwargs):
        self.fixture.render(items, cfg, folder, report, checkpoint, **kwargs)
        (folder / "monitoring.pptx").write_bytes(b"fake-office-boundary")
        report.update(reopened_structure_verified=True, stage="COMPLETE")
        checkpoint()

    def send(self, **payload):
        self.calls.append(payload)
        return {"ok": True, "action": "sent"}

    def browser(self, cfg, settings, webcfg, folder, report, checkpoint, window, words):
        from v8.period_collect import collect_with_pages
        with patch("v8.period_collect.load_page", side_effect=lambda page,cfg,k,n,t:deepcopy(self.fixture.pages[(k,n)])), \
             patch("v8.period_collect.collect_article", side_effect=self.fixture.source):
            return collect_with_pages(cfg,settings,webcfg,folder,report,checkpoint,window,words,
                    search_page=self.fixture.fake_page, article_page=self.fixture.fake_page, timeout_error=TimeoutError)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(self.fixture.patches())
        stack.enter_context(patch("v8.period_collect.collect_with_browser", side_effect=self.browser))
        from v8.analysis_engine import analyze_articles
        from v754.period_sources import load_period_source
        from v754.tests.test_analysis import FakeAnalyzer, candidate_for, response_for
        def analyze(items, spec, folder, report, checkpoint):
            self.fixture.api = FakeAnalyzer(response_for(candidate_for(load_period_source(items[0]))))
            return analyze_articles(items, spec, folder, report, checkpoint,
                                    analyzer_factory=lambda key,cfg:self.fixture.api)
        stack.enter_context(patch("v8.analysis_engine.analyze_articles", side_effect=analyze))
        stack.enter_context(patch("v8.analysis_engine.load_api_key", return_value=""))
        stack.enter_context(patch("v8.backend.verify_base"))
        stack.enter_context(patch.object(Backend, "check", return_value={}))
        stack.enter_context(patch("v8.powerpoint.render", side_effect=self.render))
        return stack

    def test_actual_saved_collection_analysis_and_v8_mail_are_linked(self):
        with self.patches():
            backend = Backend(self.fixture.root)
            runner = Runner(self.fixture.root, self.cfg, backend, self.send)
            self.assertEqual(runner.execute(now=parse_time("2026-09-17 10:00")), 0)
            self.assertEqual(runner.execute(now=parse_time("2026-09-17 10:00")), 0)
        self.assertEqual(self.fixture.source_calls, ["101"])
        self.assertEqual(len(self.calls), 1)
        record_path = next((self.fixture.root / "output_v8/runs").glob("*/run.json"))
        record = read_json(record_path)
        report = read_json(record_path.parent / record["artifact"]["summary"])
        self.assertEqual(report["api_calls"], 1)
        self.assertEqual(self.calls[0]["attachments"], [report["pptx"]])
        self.assertEqual(report["items"][0]["display_text"], self.fixture.rendered[0]["display_text"])

    def test_missing_capture_partial_from_actual_export_is_mailed_as_partial(self):
        original = self.render

        def render_with_issue(items, cfg, folder, report, checkpoint, **kwargs):
            original(items, cfg, folder, report, checkpoint, **kwargs)
            report["missing_capture_articles"] = [items[0]["id"]]
            checkpoint()

        with self.patches(), patch("v8.powerpoint.render", side_effect=render_with_issue):
            backend = Backend(self.fixture.root)
            runner = Runner(self.fixture.root, self.cfg, backend, self.send)
            self.assertEqual(runner.execute(now=parse_time("2026-09-17 10:00")), 2)
        self.assertIn("[부분 완료]", self.calls[0]["subject"])
        self.assertIn("캡처 확인 필요: 1건", self.calls[0]["body"])

    def test_real_analysis_cache_survives_export_retry(self):
        from v754.core import V7Error
        with self.patches():
            backend = Backend(self.fixture.root)
            runner = Runner(self.fixture.root, self.cfg, backend, self.send)
            with patch("v8.powerpoint.render", side_effect=V7Error("PPT_FAILED", "모사 실패")):
                self.assertEqual(runner.execute(now=parse_time("2026-09-17 10:00")), 1)
            self.assertEqual(runner.execute(now=parse_time("2026-09-17 10:00"), retry=True), 0)
        record_path = next((self.fixture.root / "output_v8/runs").glob("*/run.json"))
        record = read_json(record_path)
        report = read_json(record_path.parent / record["artifact"]["summary"])
        self.assertEqual(report["api_calls"], 0)
        self.assertEqual(report["analysis_cache_hits"], 1)
        self.assertEqual(self.fixture.source_calls, ["101"])


if __name__ == "__main__":
    unittest.main()
