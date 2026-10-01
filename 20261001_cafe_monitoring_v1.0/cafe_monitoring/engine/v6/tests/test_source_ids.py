"""6.0.3 regression tests: recorded failures + mock SDK; no paid API calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from v6.api_client import build_request
from v6.common import V6Error, atomic_json, digest, read_json
from v6.evidence import source_units, resolve_evidence
from v6.engine import (start_run, start_recheck, save_manifest, load_resume,
                       execute, read_cache, cache_path, validate_manifest)
from v6.prompts_v62 import analysis_key, make_spec
from v6.saved_audit import inspect_quotes, audit_saved_run
from v6.schema_v62 import validate_analysis, validate_spec, semantic_issues
from v6.schema_v61 import Analysis as Analysis61
from v6.tests.test_v6 import WorkspaceTest, FakeAnalyzer, analysis, analysis_v61, article, response

FIXTURE = read_json(Path(__file__).parent / "fixtures" / "recorded_602.json")["items"]


def refs(a, text):
    # A reference may span several consecutive exact source slices.
    for field in ("title", "body"):
        start = a[field].find(text)
        if start >= 0:
            return [u["id"] for u in source_units(a) if u["source"] == field
                    and u["end"] > start and u["start"] < start + len(text)]
    raise AssertionError("fixture quote absent")


def fact(a, value, text):
    return {"value": value, "evidence_ids": refs(a, text)}


class SourceIDTests(unittest.TestCase):
    def test_units_reconstruct_entire_original_with_exact_offsets(self):
        for a in [x["article"] for x in FIXTURE] + [article(title='"😀"', body='\n\t가😀"\\n\u200b' * 250), article(body="가" * 20001)]:
            with self.subTest(id=a["article_id"], size=len(a["body"])):
                units = source_units(a)
                self.assertEqual(len({u["id"] for u in units}), len(units))
                for field in ("title", "body"):
                    selected = [u for u in units if u["source"] == field]
                    self.assertEqual("".join(u["text"] for u in selected), a[field])
                    for u in selected:
                        self.assertEqual(a[field][u["start"]:u["end"]], u["text"])
                        self.assertLessEqual(len(u["text"]), 240)
                self.assertEqual(units, source_units(a))

    def test_all_five_request_bodies_unchanged_and_units_match(self):
        cfg = {"model":"gpt-5.4-mini", "reasoning_effort":"low", "focus_topics":["카메라"],
               "max_output_tokens":6000, "timeout_seconds":120, "max_body_chars":20000}
        spec = make_spec(cfg)
        for item in FIXTURE:
            a = item["article"]
            payload = json.loads(build_request(a, spec)["input"][0]["content"])
            self.assertEqual(payload["article"], {k:a[k] for k in ("title","body","written_at")})
            self.assertEqual(payload["evidence_units"], source_units(a))
            self.assertEqual(a["input_sha256"], digest(payload["article"]))

    def test_quotes_come_from_source_not_generated_punctuation(self):
        a = article(body='"경고등"\n사라짐\\n😀')
        d = analysis(a); d["symptoms"] = [fact(a, "경고등 표시", a["body"])]
        d = validate_analysis(d, a)
        binding = next(b for b in resolve_evidence(d,a)["bindings"] if b["field"] == "symptoms[0].evidence_ids")
        self.assertEqual("".join(q["text"] for q in binding["quotes"]), a["body"])

    def test_noncontiguous_evidence_preserved_as_separate_quotes(self):
        a = article(body="처음 경고. 중간 내용. 나중 소실.")
        d = analysis(a); d["situation"]["course"] = [{"value":"경고 후 소실", "evidence_ids":["B0001","B0003"]}]
        result = resolve_evidence(validate_analysis(d,a),a)
        binding = next(b for b in result["bindings"] if b["field"] == "situation.course[0].evidence_ids")
        self.assertEqual([q["text"] for q in binding["quotes"]], ["처음 경고. ","나중 소실."])

    def test_invalid_duplicate_missing_or_empty_refs_rejected(self):
        for ids in (["B9999"], ["B0001","B0001"], [], ['"B0001"']):
            a = article(); d = analysis(a); d["symptoms"]=[{"value":"무음","evidence_ids":ids}]
            with self.subTest(ids=ids), self.assertRaises(V6Error): validate_analysis(d,a)
        d = analysis(); d["symptoms"] = [{"value":"무음"}]
        with self.assertRaises(V6Error): validate_analysis(d,article())

    def test_outcome_ref_requirements(self):
        for val, ids in (("resolved", []), ("not_stated", ["B0001"])):
            d=analysis(); d["outcome"]={"value":val,"evidence_ids":ids}
            with self.assertRaises(V6Error): validate_analysis(d,article())

    def test_delivery_date_not_model_year_but_explicit_year_allowed(self):
        a=article(body="2024년 11월 초에 출고했구요."); d=analysis(a)
        d["vehicle"]["model_year"]=fact(a,"2024년",a["body"])
        with self.assertRaisesRegex(V6Error,"model_year"): validate_analysis(d,a)
        d["vehicle"]["model_year"]=None
        d["vehicle"]["delivery_date"]=fact(a,"2024년 11월 초",a["body"])
        self.assertIsNone(validate_analysis(d,a)["vehicle"]["model_year"])
        for body in ("2024년식 차량입니다.", "모델 연식: 2024", "MY2024 차량입니다."):
            a=article(body=body); d=analysis(a); d["vehicle"]["model_year"]=fact(a,"2024년",body)
            self.assertIsNotNone(validate_analysis(d,a)["vehicle"]["model_year"])

    def test_warning_not_diagnostic_but_scan_is(self):
        a=next(x["article"] for x in FIXTURE if x["article"]["article_id"]=="5494403")
        d=analysis(a)
        d["diagnostic_findings"]=[fact(a,"샤시 제어기 점검 경고", "점검하고나서 샤시 도메인 제어기 점검 뜸....")]
        with self.assertRaisesRegex(V6Error,"diagnostic_findings"): validate_analysis(d,a)
        d["symptoms"]=d["diagnostic_findings"]; d["diagnostic_findings"]=[]
        self.assertEqual(validate_analysis(d,a)["diagnostic_findings"],[])
        a=article(body="스캔결과 4번 실린더 실화 감지, 경고등이 켜졌다고 합니다.")
        d=analysis(a); d["diagnostic_findings"]=[fact(a,"4번 실린더 실화 감지",a["body"])]
        self.assertEqual(len(validate_analysis(d,a)["diagnostic_findings"]),1)

    def test_disappearance_does_not_prove_onset_conditions_or_summary(self):
        a=next(x["article"] for x in FIXTURE if x["article"]["article_id"]=="5493870")
        d=analysis(a); d["situation"]["onset_conditions"]=fact(a,"주행 중 경고 발생",a["body"])
        with self.assertRaisesRegex(V6Error,"onset_conditions"): validate_analysis(d,a)
        d["situation"]["onset_conditions"]=None
        d["situation"]["course"]=[fact(a,"주행 후 표시가 사라짐",a["body"])]
        self.assertIsNone(validate_analysis(d,a)["situation"]["onset_conditions"])
        d["summary"]="주행 중 경고가 떴다가 사라졌습니다."
        with self.assertRaisesRegex(V6Error,"summary"): validate_analysis(d,a)
        a=article(body="주행 중 경고가 떴습니다."); d=analysis(a)
        d["situation"]["onset_conditions"]=fact(a,"주행 중",a["body"])
        self.assertIsNotNone(validate_analysis(d,a)["situation"]["onset_conditions"])

    def test_known_booking_and_cause_guards_use_resolved_text(self):
        a=article(body="하이테크 예약풀..."); d=analysis(a)
        d["actions"]=[{"description":"하이테크 예약","status":"completed","evidence_ids":["B0001"]}]
        with self.assertRaises(V6Error): validate_analysis(d,a)
        d["actions"][0]["status"]="unavailable"
        self.assertEqual(validate_analysis(d,a)["actions"][0]["status"],"unavailable")

    def test_recorded_75_quotes_38_format_candidates_two_remaining(self):
        totals=[0,0,0]; by_id={}
        for item in FIXTURE:
            before=copy.deepcopy(item["response_6_1"])
            candidate,r=inspect_quotes(before,item["article"])
            self.assertEqual(before,item["response_6_1"])
            stats=[r["checked_quotes"],len(r["format_candidates"]),len(r["unresolved_quotes"])]
            totals=[x+y for x,y in zip(totals,stats)]
            by_id[item["article"]["article_id"]]=stats
        self.assertEqual(totals,[75,38,2])
        self.assertEqual(by_id["5496748"],[12,11,1])
        self.assertEqual(by_id["5496158"],[19,7,1])

    def test_format_decode_is_single_layer_source_gated(self):
        a=article(body="원문입니다.")
        good=json.dumps(a["body"],ensure_ascii=False)
        for q, count in ((good,1),(json.dumps(good),0),('"없는 문장"',0),('"원문입니다."},',0)):
            _,r=inspect_quotes({"quote":q},a)
            self.assertEqual(len(r["format_candidates"]),count)

    def test_real_remaining_semantic_errors_are_flagged(self):
        expected={"5496158":"vehicle.model_year","5494403":"diagnostic_findings[0]","5493870":"summary"}
        for item in FIXTURE:
            aid=item["article"]["article_id"]
            if aid not in expected: continue
            candidate,_=inspect_quotes(item["response_6_1"],item["article"])
            issues=semantic_issues(candidate,item["article"],lambda f:f["quote"])
            self.assertIn(expected[aid],[i["field"] for i in issues])


class CompatibilityAndAuditTests(WorkspaceTest):
    def old61(self):
        self.seed(1); folder,m=start_run(self.cfg,1)
        m["version"]="6.0.2"; s=m["spec"]
        s.update(schema_version="6.1",validator_version="2",schema=Analysis61.model_json_schema(),prompt_version="6.0.2-ko-2")
        s.pop("evidence_version",None)
        s["fingerprint"]=digest({k:v for k,v in s.items() if k not in ("fingerprint","timeout_seconds")})
        for j in m["jobs"]:
            j.pop("evidence_units",None); j["cache_key"]=analysis_key(j["article"],s)
        save_manifest(folder,m)
        return folder,m

    def test_old61_saved_response_resumes_and_request_contract_retained(self):
        folder,m=self.old61(); validate_spec(m["spec"])
        j=m["jobs"][0]; j.update(status="running",attempts=1);save_manifest(folder,m)
        from v6.engine import receipt_path
        atomic_json(receipt_path(folder,j),response(j["article"],analysis_v61(j["article"])))
        payload=json.loads(build_request(j["article"],m["spec"])["input"][0]["content"])
        self.assertNotIn("evidence_units",payload)
        folder,m=load_resume(self.cfg,m["run_id"]); fake=FakeAnalyzer()
        result=execute(self.cfg,folder,m,factory=lambda spec:fake,emit=lambda *x:None)
        self.assertEqual((result["analyzed"],fake.ids),(1,[]))

    def test_audit_never_calls_api_or_modifies_existing_run_or_cache(self):
        folder,m=self.old61(); j=m["jobs"][0];j.update(status="failed",attempts=1);save_manifest(folder,m)
        from v6.engine import receipt_path
        value=analysis_v61(j["article"])
        value["classification_evidence"]=[json.dumps(j["article"]["title"],ensure_ascii=False)]
        atomic_json(receipt_path(folder,j),response(j["article"],value))
        before={str(p):p.read_bytes() for p in self.cfg["output_dir"].rglob('*') if p.is_file()}
        with patch("v6.engine.OpenAIAnalyzer",side_effect=AssertionError("no API")):
            path,report=audit_saved_run(self.cfg,m["run_id"])
        for p,b in before.items(): self.assertEqual(Path(p).read_bytes(),b)
        self.assertTrue(path.exists()); self.assertEqual(report["api_calls"],0)
        self.assertEqual(len(report["items"][0]["format_candidates"]),1)
        self.assertFalse((self.cfg["output_dir"]/'analyses').exists())

    def test_current_result_has_exact_quotes_and_saved_request_and_reuses_cache(self):
        self.seed(1); folder,m,s,fake=self.run_new()
        j=m["jobs"][0]; d=read_json(cache_path(self.cfg["output_dir"],j["cache_key"]))
        self.assertEqual(d["evidence"],resolve_evidence(d["analysis"],j["article"]))
        self.assertEqual(d["validation"]["full_semantic_accuracy"],"not_certified")
        request=read_json(next((folder/'requests').glob('*.json')))
        self.assertEqual(request,build_request(j["article"],m["spec"]))
        self.assertEqual(self.run_new()[2]["api_attempts"],0)

    def test_even_rehashed_cache_with_wrong_resolved_text_rejected(self):
        self.seed(1); folder,m,_,_=self.run_new(); j=m["jobs"][0]
        p=cache_path(self.cfg["output_dir"],j["cache_key"]); d=read_json(p)
        d["evidence"]["bindings"][0]["quotes"][0]["text"]="wrong"
        d.pop("artifact_sha256"); d["artifact_sha256"]=digest(d);atomic_json(p,d)
        self.assertIsNone(read_cache(self.cfg["output_dir"],j["cache_key"],j["article"],m["spec"]))

    def test_manifest_evidence_tampering_rejected(self):
        self.seed(1); _,m=start_run(self.cfg,1)
        m["jobs"][0]["evidence_units"][0]["text"]="wrong"
        with self.assertRaises(V6Error): validate_manifest(m)

    def test_old61_recheck_uses_new_schema_without_touching_old(self):
        old_folder,old=self.old61();before=(old_folder/'manifest.json').read_bytes()
        folder,m=start_recheck(self.cfg,old["run_id"])
        self.assertEqual(m["spec"]["schema_version"],"6.2")
        self.assertEqual(m["jobs"][0]["article"],old["jobs"][0]["article"])
        self.assertEqual((old_folder/'manifest.json').read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
