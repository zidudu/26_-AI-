"""Offline inspection of saved responses. Never promotes a result to the API cache."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

from .api_client import decode_response
from .common import V6Error, atomic_json, digest, now, read_json
from .schema import semantic_issues, validate_analysis


def inspect_quotes(value, article):
    candidate = deepcopy(value)
    changes, unresolved = [], []
    corpus = " ".join((article["title"] + "\n" + article["body"]).split())
    checked = 0

    def quote(q, path):
        nonlocal checked
        if q is None:
            return q
        checked += 1
        if not isinstance(q, str) or not q.strip():
            unresolved.append({"field": path, "original": q, "reason": "empty_or_invalid"})
            return q
        if " ".join(q.split()) in corpus:
            return q
        try:
            decoded = json.loads(q)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, str) and decoded.strip() and " ".join(decoded.split()) in corpus:
            changes.append({"field": path, "original": q, "candidate": decoded,
                            "method": "one_json_string_decode_then_full_source_match"})
            return decoded
        unresolved.append({"field": path, "original": q, "reason": "not_a_full_source_quote"})
        return q

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in list(node.items()):
                child = f"{path}.{k}" if path else k
                if k == "quote":
                    node[k] = quote(v, child)
                elif k in ("classification_evidence", "focus_evidence") and isinstance(v, list):
                    node[k] = [quote(x, f"{child}[{i}]") for i, x in enumerate(v)]
                else:
                    walk(v, child)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    walk(candidate)
    return candidate, {"checked_quotes": checked, "format_candidates": changes,
                       "unresolved_quotes": unresolved}


def audit_saved_run(cfg, run_id=None):
    from .engine import manifests, receipt_path, validate_manifest
    choices = [(p, m) for p, m in manifests(cfg["output_dir"])
               if m["jobs"] and (not run_id or m["run_id"] == run_id)]
    if not choices:
        raise V6Error("NO_AUDIT_SOURCE", "점검할 실행을 찾지 못했습니다. output_v6/runs의 실행 ID를 확인하세요.")
    folder, m = choices[0]
    validate_manifest(m)
    report = {"version": "6.1.0", "audited_at": now(), "run_id": m["run_id"],
              "schema_version": m["spec"]["schema_version"], "api_calls": 0,
              "scope": "저장 응답의 형식 점검 및 알려진 의미 오류 점검. 원본·분석 이력·완료 상태 변경 없음.",
              "items": []}
    for job in m["jobs"]:
        item = {"article_id": job["article"]["article_id"], "original_status": job["status"],
                "human_review": "required"}
        try:
            raw = read_json(receipt_path(folder, job))
            value = decode_response(raw)
            candidate, quote_report = inspect_quotes(value, job["article"])
            item.update(quote_report)
            item["raw_response_sha256"] = digest(raw)
            item["candidate_analysis"] = candidate
            try:
                validate_analysis(candidate, job["article"], m["spec"]["schema_version"])
                item["format_and_original_guards"] = "pass"
            except V6Error as exc:
                item.update(format_and_original_guards="fail", code=exc.code, message=str(exc))
            if m["spec"]["schema_version"] == "6.1":
                item["known_semantic_issues"] = semantic_issues(candidate, job["article"], lambda f: f["quote"])
            elif m["spec"]["schema_version"] == "6.0":
                item["known_semantic_issues"] = None
                item["semantic_check_note"] = "6.0 응답은 기존 규격 검사와 인용 형식만 점검합니다. 의미는 수동 검토가 필요합니다."
            elif m["spec"]["schema_version"] == "6.3":
                from .drafts import make_draft
                draft = make_draft(job["article"], candidate, "6.3", {"mode": "offline_audit", "api_calls": 0})
                item["known_semantic_issues"] = draft["review"]["detected_issues"]
                item["review_notes"] = draft["review"]["notes"]
            else:
                from .evidence import evidence_text, resolve_evidence
                units = resolve_evidence(candidate, job["article"])["units"]
                item["known_semantic_issues"] = semantic_issues(candidate, job["article"], lambda f: evidence_text(f, units))
        except (OSError, ValueError, KeyError, TypeError, V6Error) as exc:
            item.update(format_and_original_guards="unavailable", code=getattr(exc, "code", "RECEIPT_UNAVAILABLE"))
        report["items"].append(item)
    stamp = now().replace(":", "").replace("-", "").split("+")[0].replace(".", "_")
    target = Path(cfg["output_dir"]) / "audits" / f"{stamp}_{uuid4().hex[:8]}.json"
    atomic_json(target, report)
    return target, report
