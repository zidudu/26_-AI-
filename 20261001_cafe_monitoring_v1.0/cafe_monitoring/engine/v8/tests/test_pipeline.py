"""실제 웹/API/Office 호출 없는 제어 흐름 검사. 플랫폼 기능시험은 별도입니다."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from v8.configuration import DEFAULT, KST, V8Error, read_json, write_json, validate, parse_time
from v8.pipeline import Runner, sha
from v8.schedule import cutoff, explicit_window, previous_cutoff, run_id, scope, scheduled_window


def config():
    cfg = deepcopy(DEFAULT)
    cfg["schedule"]["initial_start"] = "2026-09-17 09:00"
    cfg["mail"]["to"] = ["test@example.invalid"]
    return cfg


NOW = parse_time("2026-09-18 10:00")


class FakeBackend:
    cafe_id = "20179506"
    words = ["SCC", "크루즈"]

    def __init__(self):
        self.calls = []
        self.mode = "completed"
        self.fail_export = False
        self.fail_collect = False
        self.break_after_export = False

    def check(self):
        self.calls.append("check")

    def collect(self, folder, start, end):
        self.calls.append("collect")
        write_json(folder / "collection.json", {"start": start.isoformat(), "end": end.isoformat(),
                   "complete": not self.fail_collect})
        if self.fail_collect:
            raise V8Error("LOGIN_REQUIRED", "로그인이 필요합니다.")

    def collection_ok(self, folder, start, end):
        data = read_json(folder / "collection.json")
        return data["complete"] and data["start"] == start.isoformat() and data["end"] == end.isoformat()

    def export(self, collection, folder):
        self.calls.append("export")
        if self.fail_export:
            raise V8Error("PPT_FAILED", "PPT 생성 실패")
        dates = read_json(collection / "collection.json")
        empty = self.mode == "completed_empty"
        items = [] if empty else [{"id": "5500144"}]
        report = {"status": self.mode, "stage": "COMPLETE", "cafe_id": self.cafe_id,
                  "keywords": self.words, "window": {"start": dates["start"], "end": dates["end"]},
                  "collection_complete": True, "range_search_complete": True,
                  "articles_verified_complete": True, "selected_articles": len(items), "items": items,
                  "reopened_structure_verified": not empty, "slides": len(items)}
        if not empty:
            ppt = folder / "monitoring.pptx"
            # 모사 파일이며 실제 PPT 검증을 의미하지 않습니다.
            ppt.write_bytes(b"fake-office-artifact-for-control-flow-tests")
            report["pptx"] = str(ppt)
        if self.mode == "unverified":
            report.update(status="completed", reopened_structure_verified=False)
        if self.mode == "incomplete":
            report.update(status="completed_empty", collection_complete=False, selected_articles=0, items=[])
        if self.mode == "wrong_period":
            report["status"] = "completed"
            report["window"]["end"] = parse_time("2026-09-17 08:00").isoformat()
        write_json(folder / "summary.json", report)
        if self.break_after_export:
            raise KeyboardInterrupt()
        return report, 2 if self.mode == "partial" else 0


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v8_한글 공백_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = config()
        self.backend = FakeBackend()
        self.sent = []
        self.fail_mail = False
        self.runner = Runner(self.root, self.cfg, self.backend, self.send)

    def send(self, **payload):
        self.sent.append(payload)
        if not payload["display_only"]:
            self.assertEqual(self.record()["mail"]["status"], "sending")
        if self.fail_mail:
            raise TimeoutError("simulated unknown response")
        return {"ok": True, "action": "displayed" if payload["display_only"] else "sent"}

    def execute(self, **kw):
        with redirect_stdout(StringIO()):
            return self.runner.execute(now=NOW, **kw)

    def record(self):
        return read_json(next(self.runner.runs.glob("*/run.json")))

    def state(self):
        return read_json(self.runner.state_path)

    def test_complete_then_same_cutoff_is_noop(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.backend.calls.count("collect"), 1)
        self.assertEqual(self.backend.calls.count("export"), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state()["cursor"], parse_time("2026-09-18 09:00").isoformat())

    def test_same_explicit_run_never_resends(self):
        self.execute()
        self.assertEqual(self.execute(identifier=self.record()["id"]), 0)
        self.assertEqual(len(self.sent), 1)

    def test_empty_has_no_mail_and_advances(self):
        self.backend.mode = "completed_empty"
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.record()["status"], "completed_empty")
        self.assertIsNone(self.state()["active"])

    def test_partial_attaches_exact_current_ppt(self):
        stale = self.root / "newest.pptx"
        stale.write_bytes(b"unrelated")
        self.backend.mode = "partial"
        self.assertEqual(self.execute(), 2)
        self.assertIn("[부분 완료]", self.sent[0]["subject"])
        self.assertIn(self.record()["id"], self.sent[0]["attachments"][0])
        self.assertNotEqual(self.sent[0]["attachments"], [str(stale)])
        self.assertIsNone(self.state()["active"])

    def test_partial_hold_then_release_reuses_ppt(self):
        self.backend.mode = "partial"
        self.cfg["mail"]["send_partial"] = False
        self.assertEqual(self.execute(), 2)
        self.assertEqual(self.sent, [])
        self.cfg["mail"]["send_partial"] = True
        self.assertEqual(self.execute(), 2)
        self.assertEqual(self.backend.calls.count("export"), 1)
        self.assertEqual(len(self.sent), 1)

    def test_unverified_ppt_is_not_mailed(self):
        self.backend.mode = "unverified"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.state()["cursor"])

    def test_incomplete_zero_is_not_empty_success(self):
        self.backend.mode = "incomplete"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.state()["cursor"])

    def test_other_period_result_is_rejected(self):
        self.backend.mode = "wrong_period"
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.record()["error"]["code"], "RESULT_MISMATCH")
        self.assertEqual(self.sent, [])

    def test_failed_export_does_not_retry_automatically(self):
        self.backend.fail_export = True
        self.assertEqual(self.execute(), 1)
        self.backend.fail_export = False
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.backend.calls.count("export"), 1)

    def test_explicit_retry_uses_saved_collection(self):
        self.backend.fail_export = True
        self.execute()
        self.backend.fail_export = False
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(self.backend.calls.count("collect"), 1)
        self.assertEqual(self.backend.calls.count("export"), 2)

    def test_failed_collection_requires_retry_and_keeps_attempt(self):
        self.backend.fail_collect = True
        self.assertEqual(self.execute(), 1)
        self.backend.fail_collect = False
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(self.backend.calls.count("collect"), 2)
        self.assertEqual(len([a for a in self.record()["attempts"] if a["kind"] == "collect"]), 2)

    def test_completed_export_after_crash_is_recovered(self):
        self.backend.break_after_export = True
        self.assertEqual(self.execute(), 130)
        self.backend.break_after_export = False
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(self.backend.calls.count("export"), 1)

    def test_preview_then_send_has_no_second_export(self):
        self.assertEqual(self.execute(preview=True), 0)
        self.assertIsNone(self.state()["cursor"])
        self.assertTrue(self.sent[0]["display_only"])
        self.assertEqual(self.execute(), 0)
        self.assertFalse(self.sent[1]["display_only"])
        self.assertEqual(self.backend.calls.count("export"), 1)

    def test_unknown_mail_never_retries_even_with_retry_flag(self):
        self.fail_mail = True
        self.assertEqual(self.execute(), 3)
        self.fail_mail = False
        self.assertEqual(self.execute(retry=True), 3)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.state()["cursor"])

    def test_user_confirms_sent_no_second_mail(self):
        self.fail_mail = True
        self.execute()
        with redirect_stdout(StringIO()):
            self.runner.resolve_mail(self.record()["id"], "sent")
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.sent), 1)

    def test_user_confirms_not_sent_reuses_artifact(self):
        self.fail_mail = True
        self.execute()
        with redirect_stdout(StringIO()):
            self.runner.resolve_mail(self.record()["id"], "not-sent")
        self.fail_mail = False
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.backend.calls.count("export"), 1)

    def test_changed_ppt_after_preview_is_not_sent(self):
        self.execute(preview=True)
        Path(self.sent[0]["attachments"][0]).write_bytes(b"modified")
        self.assertEqual(self.execute(), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.record()["error"]["code"], "ARTIFACT_CHANGED")

    def test_final_state_write_crash_recovers_cursor(self):
        self.execute()
        record = self.record()
        write_json(self.runner.state_path, {"scope": self.runner.identity, "cursor": None, "active": record["id"]})
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state()["cursor"], record["end"])

    def test_scope_change_does_not_reuse_wrong_cursor(self):
        self.execute()
        self.runner.identity = scope("123456", self.backend.words)
        with self.assertRaisesRegex(V8Error, "카페·검색어"):
            self.execute()

    def test_interval_remains_fixed_during_later_retry(self):
        self.backend.fail_export = True
        self.execute()
        end = self.record()["end"]
        self.backend.fail_export = False
        with redirect_stdout(StringIO()):
            self.assertEqual(self.runner.execute(now=parse_time("2026-09-21 12:00"), retry=True), 0)
        self.assertEqual(self.record()["end"], end)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()

    def test_late_start_keeps_nine_am(self):
        self.assertEqual(cutoff(NOW, self.cfg["schedule"]), parse_time("2026-09-18 09:00"))

    def test_monday_before_cutoff_uses_friday(self):
        self.assertEqual(cutoff(parse_time("2026-09-21 08:59"), self.cfg["schedule"]), parse_time("2026-09-18 09:00"))

    def test_weekend_is_in_monday_window(self):
        state = {"cursor": parse_time("2026-09-18 09:00").isoformat()}
        lo, hi = scheduled_window(self.cfg, state, parse_time("2026-09-21 09:10"))
        self.assertEqual((hi-lo).days, 3)

    def test_daily_mode_includes_sunday_boundary(self):
        self.cfg["schedule"]["weekdays_only"] = False
        self.assertEqual(cutoff(parse_time("2026-09-20 09:10"), self.cfg["schedule"]), parse_time("2026-09-20 09:00"))

    def test_missed_week_uses_last_successful_cursor(self):
        state = {"cursor": parse_time("2026-09-11 09:00").isoformat()}
        lo, hi = scheduled_window(self.cfg, state, NOW)
        self.assertEqual((hi-lo).days, 7)

    def test_bad_or_future_period_is_rejected(self):
        for lo, hi in [("2026-09-18 10:00", "2026-09-18 09:00"), ("2026-09-18 09:00", "2026-09-19 09:00")]:
            with self.subTest(lo=lo, hi=hi), self.assertRaises(V8Error):
                explicit_window(lo, hi, NOW)

    def test_keyword_order_has_same_identity(self):
        a = scope("20179506", ["SCC", "크루즈"])
        b = scope("20179506", ["크루즈", "SCC", "SCC"])
        self.assertEqual(run_id(a, NOW, NOW), run_id(b, NOW, NOW))

    def test_config_rejects_string_boolean_and_invalid_recipient(self):
        for key, value in [("send_partial", "false"), ("timeout_seconds", True), ("to", ["a\nb"])]:
            cfg = config()
            cfg["mail"][key] = value
            with self.subTest(key=key), self.assertRaises(V8Error):
                validate(cfg)


if __name__ == "__main__":
    unittest.main()
