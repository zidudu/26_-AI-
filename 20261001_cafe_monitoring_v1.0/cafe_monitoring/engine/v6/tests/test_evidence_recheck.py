"""근거 누락/의미 모순/버전 간 재검증 회귀 검사. 유료 API를 호출하지 않습니다."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from v6.api_client import decode_response
from v6.common import V6Error, atomic_json, digest, read_json
from v6.engine import (execute, inspect_recheck, load_resume, receipt_path,
                       save_manifest, start_recheck, start_run)
from v6.legacy_schema import Analysis as LegacyAnalysis
from v6.prompts_v62 import analysis_key
from v6.schema_v62 import Analysis, validate_analysis, validate_spec
from v6.schema_v61 import validate_analysis as validate_v61
from v6.tests.test_v6 import WorkspaceTest, FakeAnalyzer, analysis_v61 as analysis, article, response


def legacy_analysis(a):
    return {"document_type": "issue_experience", "quality_issue": "yes", "firsthand_experience": "yes",
            "vehicle": {"model": None, "model_year": None, "mileage": None}, "symptoms": [],
            "parts_or_functions": [], "situation": None,
            "reported_cause": {"description": None, "basis": "not_stated"}, "actions": [],
            "outcome": "not_stated", "summary": "작성자의 문제 경험입니다.",
            "focus_relevance": "unrelated", "focus_reason": "관심 기능은 명시되지 않았습니다.",
            "evidence": [{"field": "classification", "quote": a["title"]}],
            "needs_review": False, "review_reasons": []}


def legacy_run(cfg, count=5):
    folder, m = start_run(cfg, count)
    spec = m["spec"]
    spec.update(schema_version="6.0", validator_version="1", prompt_version="6.0.0-ko-1",
                schema=LegacyAnalysis.model_json_schema(), instructions="이전 버전 테스트용 지시문")
    spec["fingerprint"] = digest({k: v for k, v in spec.items() if k not in ("fingerprint", "timeout_seconds")})
    m["version"] = "6.0.1"
    for j in m["jobs"]:
        j["cache_key"] = analysis_key(j["article"], spec)
    save_manifest(folder, m)
    return folder, m


class PairedEvidenceTests(unittest.TestCase):
    def test_each_fact_item_requires_own_quote(self):
        for field in ("symptoms", "parts_or_functions", "diagnostic_findings"):
            d = analysis()
            d[field] = [{"value": "클락션 고장", "quote": "클락션 고장"}, {"value": "센터 방문"}]
            with self.subTest(field=field), self.assertRaisesRegex(V6Error, field + r"\.1\.quote"):
                validate_v61(d, article())

    def test_situation_missing_and_blank_quote_have_exact_path(self):
        for quote in (None, "", "   ", "원문에 없는 내용"):
            d = analysis(); d["situation"] = {"value": "클락션 고장"}
            if quote is not None:
                d["situation"]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaisesRegex(V6Error, r"situation\.quote"):
                validate_v61(d, article())

    def test_vehicle_value_cannot_omit_evidence(self):
        d = analysis(); d["vehicle"]["model"] = {"value": "차종"}
        with self.assertRaisesRegex(V6Error, r"vehicle\.model\.quote"):
            validate_v61(d, article())

    def test_outcome_requires_quote_except_not_stated(self):
        for value in ("resolved", "unresolved", "temporary_improvement", "unclear"):
            d = analysis(); d["outcome"] = {"value": value, "quote": None}
            with self.subTest(value=value), self.assertRaisesRegex(V6Error, r"outcome.quote"):
                validate_v61(d, article())
        self.assertEqual(validate_v61(analysis(), article())["outcome"]["value"], "not_stated")

    def test_whitespace_normalization_does_not_rewrite_quote(self):
        a = article(body="경고등이\n\n계속 켜집니다.")
        d = analysis(a); d["symptoms"] = [{"value": "경고등 지속", "quote": "경고등이 계속 켜집니다."}]
        self.assertEqual(validate_v61(d, a)["symptoms"], d["symptoms"])

    def test_reusing_actual_quote_across_fields_allowed(self):
        a = article(); d = analysis(a)
        d["symptoms"] = [{"value": "클락션 무음", "quote": a["body"]}]
        d["situation"] = {"value": "클락션을 눌렀을 때 소리 없음", "quote": a["body"]}
        self.assertTrue(validate_v61(d, a)["needs_review"])

    def test_unknown_cause_is_null_not_inconsistent_pair(self):
        d = analysis(); d["reported_cause"] = {"description": None, "basis": "not_stated", "quote": None}
        with self.assertRaises(V6Error):
            validate_v61(d, article())
        self.assertIsNone(validate_v61(analysis(), article())["reported_cause"])

    def test_legacy_failures_identify_three_observed_fields(self):
        for field in ("situation", "parts_or_functions", "outcome"):
            a = article(); d = legacy_analysis(a)
            d[field] = {"situation": "센터 방문", "parts_or_functions": ["클락션"], "outcome": "unresolved"}[field]
            with self.subTest(field=field), self.assertRaisesRegex(V6Error, field):
                validate_v61(d, a, "6.0")


class ActionAndCauseTests(unittest.TestCase):
    def action_case(self, body, description, status):
        a = article(body=body); d = analysis(a)
        d["actions"] = [{"description": description, "status": status, "quote": body}]
        return a, d

    def test_booking_unavailable_is_not_completed_or_planned(self):
        for text in ("하이테크 예약풀...", "센터 예약이 마감입니다.", "예약 불가", "예약 못 했어요"):
            for state in ("completed", "planned"):
                a, d = self.action_case(text, "센터 예약", state)
                with self.subTest(text=text, state=state), self.assertRaisesRegex(V6Error, r"actions\[0\].status"):
                    validate_v61(d, a)

    def test_booking_unavailable_saved_with_proper_status(self):
        a, d = self.action_case("하이테크 예약풀...", "하이테크 예약", "unavailable")
        self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "unavailable")

    def test_advice_is_not_completion(self):
        for text, description in (("하이테크 가라함", "하이테크 방문"), ("센터에 신고하라고 했습니다.", "센터 신고")):
            a, d = self.action_case(text, description, "completed")
            with self.subTest(text=text), self.assertRaises(V6Error):
                validate_v61(d, a)
            d["actions"][0]["status"] = "recommended"
            self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "recommended")

    def test_advice_followed_by_actual_action_not_rejected(self):
        a, d = self.action_case("센터에 신고하라고 해서 신고했습니다.", "센터 신고", "completed")
        self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "completed")

    def test_booking_initially_full_then_completed_not_rejected(self):
        a, d = self.action_case("예약 풀이라 기다리다가 오늘 예약 완료했습니다.", "센터 예약", "completed")
        self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "completed")

    def test_booking_unavailable_now_with_explicit_later_plan(self):
        a, d = self.action_case("예약 풀이라 다음주 다시 예약할 예정입니다.", "다음주 예약 재시도", "planned")
        self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "planned")

    def test_unrelated_completed_repair_with_booking_context_not_rejected(self):
        a, d = self.action_case("센서 교체 했는데 경고등 안사라짐. 하이테크 가라함. 하이테크 예약풀", "센서 교체", "completed")
        self.assertEqual(validate_v61(d, a)["actions"][0]["status"], "completed")

    def test_warning_after_inspection_is_not_reported_cause(self):
        a = article(body="점검하고나서 샤시 도메인 제어기 점검 뜸....")
        d = analysis(a)
        d["reported_cause"] = {"description": "샤시 도메인 제어기 경고", "basis": "workshop_report", "quote": a["body"]}
        with self.assertRaisesRegex(V6Error, "reported_cause.quote"):
            validate_v61(d, a)
        d["reported_cause"] = None
        d["symptoms"] = [{"value": "점검 후 추가 경고 표시", "quote": a["body"]}]
        self.assertIsNone(validate_v61(d, a)["reported_cause"])

    def test_explicit_reported_cause_preserved(self):
        a = article(body="정비소에서 센서 단선 때문에 경고등이 떴다고 했습니다.")
        d = analysis(a)
        d["reported_cause"] = {"description": "센서 단선", "basis": "workshop_report", "quote": a["body"]}
        self.assertEqual(validate_v61(d, a)["reported_cause"]["basis"], "workshop_report")

    def test_cause_with_warning_in_same_sentence_not_rejected(self):
        a = article(body="EGR 막힘으로 경고등이 떴다고 정비소에서 알려줬습니다.")
        d = analysis(a)
        d["reported_cause"] = {"description": "EGR 막힘으로 경고 발생", "basis": "workshop_report", "quote": a["body"]}
        self.assertEqual(validate_v61(d, a)["reported_cause"]["basis"], "workshop_report")

    def test_review_reasons_remain_valid_when_loaded_again(self):
        d = analysis(); d["review_reasons"] = [f"검토 사유 {i}" for i in range(20)]
        saved = validate_v61(d, article())
        self.assertEqual(validate_v61(saved, article()), saved)

    def test_diagnostic_finding_does_not_require_a_cause(self):
        a = article(body="스캔결과 4번 실린더 실화가 감지되었다고해서 4번 점화코일 교체했습니다.")
        d = analysis(a)
        d["diagnostic_findings"] = [{"value": "4번 실린더 실화 감지", "quote": a["body"]}]
        self.assertIsNone(validate_v61(d, a)["reported_cause"])


class RecheckTests(WorkspaceTest):
    def test_old_run_resumes_with_old_schema_and_receipt_without_api(self):
        self.seed(1); folder, m = legacy_run(self.cfg, 1)
        j = m["jobs"][0]; j.update(status="running", attempts=1)
        save_manifest(folder, m)
        atomic_json(receipt_path(folder, j), response(j["article"], legacy_analysis(j["article"])))
        folder, loaded = load_resume(self.cfg)
        fake = FakeAnalyzer()
        s = execute(self.cfg, folder, loaded, factory=lambda spec: fake, emit=lambda *a: None)
        self.assertEqual((s["analyzed"], fake.ids), (1, []))
        self.assertEqual(loaded["spec"]["schema_version"], "6.0")

    def test_same_five_snapshots_new_schema_old_results_preserved_repeat_zero_calls(self):
        self.seed(5); old_folder, old = legacy_run(self.cfg, 5)
        for i, j in enumerate(old["jobs"]):
            j["status"] = "done" if i < 2 else "failed"
        save_manifest(old_folder, old)
        before = (old_folder / "manifest.json").read_bytes()
        # 입력 폴더가 바뀌어도 재검증에는 과거 실행의 원문을 사용합니다.
        self.cfg["input_dirs"] = [self.root / "missing"]
        selected, plan, spec = inspect_recheck(self.cfg, old["run_id"])
        self.assertEqual([a["input_sha256"] for a in selected], [j["article"]["input_sha256"] for j in old["jobs"]])
        self.assertEqual(spec["schema_version"], "6.2")
        folder, m = start_recheck(self.cfg, old["run_id"])
        fake = FakeAnalyzer()
        summary = execute(self.cfg, folder, m, factory=lambda spec: fake, emit=lambda *a: None)
        self.assertEqual((summary["analyzed"], len(fake.ids)), (5, 5))
        self.assertEqual(plan["recheck_of"], old["run_id"])
        self.assertEqual((old_folder / "manifest.json").read_bytes(), before)
        folder2, m2 = start_recheck(self.cfg, old["run_id"])
        fake2 = FakeAnalyzer()
        summary2 = execute(self.cfg, folder2, m2, factory=lambda spec: fake2, emit=lambda *a: None)
        self.assertEqual((summary2["selected"], fake2.ids), (0, []))
        self.assertEqual(summary2["plan"]["cached_articles"], 5)

    def test_recheck_default_prefers_latest_older_spec_run(self):
        self.seed(2); _, old = legacy_run(self.cfg, 2)
        _, current = start_recheck(self.cfg, old["run_id"])
        _, plan, _ = inspect_recheck(self.cfg)
        self.assertEqual(plan["recheck_of"], old["run_id"])

    def test_pending_recheck_blocks_another_recheck(self):
        self.seed(2); _, old = legacy_run(self.cfg, 2)
        start_recheck(self.cfg, old["run_id"])
        with self.assertRaisesRegex(V6Error, "04_resume"):
            start_recheck(self.cfg, old["run_id"])

    def test_recheck_failure_not_silently_retried(self):
        self.seed(1); _, old = legacy_run(self.cfg, 1)
        folder, m = start_recheck(self.cfg, old["run_id"])
        bad = response(m["jobs"][0]["article"])
        bad["output"][0]["content"][0]["text"] = '{}'
        execute(self.cfg, folder, m, factory=lambda spec: FakeAnalyzer([bad]), emit=lambda *a: None)
        with self.assertRaisesRegex(V6Error, "07_retry"):
            start_recheck(self.cfg, old["run_id"])

    def test_recheck_rejects_damaged_snapshot_and_does_not_change_old_run(self):
        self.seed(1); folder, old = legacy_run(self.cfg, 1)
        old["jobs"][0]["article"]["body"] = "tampered"
        save_manifest(folder, old)
        before = (folder / "manifest.json").read_bytes()
        with self.assertRaises(V6Error):
            start_recheck(self.cfg, old["run_id"])
        self.assertEqual((folder / "manifest.json").read_bytes(), before)

    def test_changed_spec_and_wrong_cafe_rejected(self):
        self.seed(1); folder, old = legacy_run(self.cfg, 1)
        bad = copy.deepcopy(old["spec"]); bad["instructions"] += "changed"
        with self.assertRaises(V6Error):
            validate_spec(bad)
        self.cfg["cafe_id"] = "9"
        with self.assertRaises(V6Error):
            start_recheck(self.cfg, old["run_id"])

    def test_recheck_long_source_not_silently_dropped(self):
        self.seed(1); _, old = legacy_run(self.cfg, 1)
        self.cfg["max_body_chars"] = 1
        with self.assertRaises(V6Error):
            start_recheck(self.cfg, old["run_id"])


if __name__ == "__main__":
    unittest.main()
