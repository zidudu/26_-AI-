"""분석 캐시 + 실행별 대기 목록. 원본/V5 이력은 읽기만 합니다.

응답을 받으면 receipt -> 검증된 analysis -> manifest 순서로 원자적 저장합니다.
중간에 종료돼도 receipt 또는 analysis가 있으면 API를 다시 호출하지 않습니다.
응답 자체가 유실된 요청은 unknown으로 남겨 사용자가 재시도를 선택하게 합니다.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from . import VERSION, COMPATIBLE_VERSIONS
from .api_client import OpenAIAnalyzer, decode_response, usage_from_response, build_request
from .common import V6Error, append_event, atomic_json, digest, now, read_json, text_hash
from .credentials import load_api_key
from .prompts import analysis_key, make_spec
from .schema import LABELS, validate_analysis, validate_spec
from .source import scan_sources
from .evidence import resolve_evidence, source_units

STATES = {"pending", "running", "done", "failed", "unknown"}


def cache_path(out, key):
    return Path(out) / "analyses" / (key + ".json")


def read_cache(out, key, article, spec):
    try:
        d = read_json(cache_path(out, key))
        checksum = d.pop("artifact_sha256")
        if (d["cache_key"] != key or d["source"]["input_sha256"] != article["input_sha256"] or
                d["spec_fingerprint"] != spec["fingerprint"] or digest(d) != checksum or
                d["source"]["cafe_id"] != article["cafe_id"] or d["source"]["article_id"] != article["article_id"]):
            return None
        validate_analysis(d["analysis"], article, spec["schema_version"])
        if spec["schema_version"] == "6.2" and d.get("evidence") != resolve_evidence(d["analysis"], article):
            return None
        d["artifact_sha256"] = checksum
        return d
    except (OSError, ValueError, KeyError, TypeError, V6Error):
        return None


def manifests(out):
    for path in sorted((Path(out) / "runs").glob("*/manifest.json"), reverse=True):
        try:
            d = read_json(path)
            if d.get("version") not in COMPATIBLE_VERSIONS or not isinstance(d.get("jobs"), list):
                continue
            yield path.parent, d
        except (OSError, ValueError, AttributeError):
            continue


def inspect_work(cfg, limit):
    articles, scan = scan_sources(cfg)
    spec = make_spec(cfg)
    blocked = set()
    unfinished = []
    for folder, manifest in manifests(cfg["output_dir"]):
        for job in manifest["jobs"]:
            if job["status"] in ("pending", "running") and manifest["spec"]["fingerprint"] == spec["fingerprint"]:
                unfinished.append(manifest["run_id"])
            if job["status"] in ("failed", "unknown"):
                blocked.add(job["cache_key"])
    pending, too_long = [], []
    cached = held = damaged = 0
    for article in articles:
        key = analysis_key(article, spec)
        if read_cache(cfg["output_dir"], key, article, spec):
            cached += 1
        elif key in blocked:
            held += 1
        elif len(article["body"]) > spec["max_body_chars"]:
            too_long.append({"article_id": article["article_id"], "chars": len(article["body"]), "code": "BODY_TOO_LONG"})
        else:
            if cache_path(cfg["output_dir"], key).exists():
                damaged += 1
            pending.append(article)
    plan = {**scan, "cached_articles": cached, "held_errors": held, "too_long": too_long,
            "damaged_cache": damaged, "available_new": len(pending), "selected": min(limit, len(pending)),
            "not_selected": max(0, len(pending) - limit), "requested": limit,
            "unfinished_run_ids": sorted(set(unfinished))}
    return pending[:limit], plan, spec


def start_run(cfg, limit):
    articles, plan, spec = inspect_work(cfg, limit)
    if plan["unfinished_run_ids"]:
        raise V6Error("UNFINISHED_RUN", "같은 분석 설정의 미처리 대상이 있습니다. 04_resume_v6.bat로 재개하세요.")
    if not plan["valid_files"]:
        raise V6Error("NO_SOURCE", "유효한 게시글 JSON이 없습니다. V5와 같은 폴더에 복사하고 06_preview_v6.bat로 확인하세요.")
    run_id = now().replace("-", "").replace(":", "").replace("T", "_").split("+")[0].replace(".", "_") + "_" + uuid4().hex[:8]
    folder = cfg["output_dir"] / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    m = {"version": VERSION, "run_id": run_id, "created_at": now(), "updated_at": now(),
         "status": "ready", "spec": spec, "plan": plan, "jobs": []}
    for article in articles:
        m["jobs"].append({"cache_key": analysis_key(article, spec), "article": article,
                          "status": "pending", "attempts": 0, "code": None, "message": None,
                          "calls": [], "result_file": None})
    save_manifest(folder, m)
    append_event(folder / "events.jsonl", "run_created", run_id=run_id,
                 requested=limit, selected=len(articles), model=spec["model"])
    return folder, m


def save_manifest(folder, m):
    if m["spec"]["schema_version"] in ("6.2", "6.3"):
        for job in m["jobs"]:
            units = source_units(job["article"])
            job.setdefault("evidence_units", units)
            if job["evidence_units"] != units:
                raise V6Error("PLAN_CHANGED", "저장된 원문 번호 목록이 입력 본문과 다릅니다.")
    m["updated_at"] = now()
    atomic_json(Path(folder) / "manifest.json", m)


def receipt_path(folder, job):
    return Path(folder) / "responses" / (job["cache_key"] + "_" + str(job["attempts"]) + ".json")


def record_usage(job, raw):
    receipt = {"attempt": job["attempts"], "response_id": raw.get("id"),
               "actual_model": raw.get("model"), "usage": usage_from_response(raw)}
    job["calls"] = [call for call in job["calls"] if call["attempt"] != job["attempts"]] + [receipt]


def finish_from_response(out, folder, m, job, raw):
    if not raw.get("_local_image_only"):
        record_usage(job, raw)
    analysis = validate_analysis(decode_response(raw), job["article"], m["spec"]["schema_version"])
    a = job["article"]
    result = {"version": VERSION, "cache_key": job["cache_key"], "analyzed_at": now(),
              "run_id": m["run_id"], "spec_fingerprint": m["spec"]["fingerprint"],
              "prompt_version": m["spec"]["prompt_version"], "schema_version": m["spec"]["schema_version"],
              "requested_model": m["spec"]["model"], "actual_model": raw.get("model"),
              "response_id": raw.get("id"), "usage": usage_from_response(raw),
              "source": a, "analysis": analysis,
              "human_review": {"status": "not_reviewed"}}
    if m["spec"]["schema_version"] == "6.2":
        result["evidence"] = resolve_evidence(analysis, a)
        result["validation"] = {"structure_and_references": "pass", "known_semantic_guards": "pass",
                                "full_semantic_accuracy": "not_certified", "human_review": "not_reviewed"}
    if m["spec"]["schema_version"] == "6.3":
        from .drafts import make_draft
        result["draft"] = make_draft(a, analysis, "6.3", {
            "mode": "image_only_local" if raw.get("_local_image_only") else "api_response",
            "original_run_id": m["run_id"], "original_prompt_version": m["spec"]["prompt_version"],
            "response_id": raw.get("id"), "api_calls": 0 if raw.get("_local_image_only") else 1})
        result["validation"] = {"structure": "pass", "meaning": "human_review_pending",
                                "detected_issues": result["draft"]["review"]["detected_issues"]}
    result["artifact_sha256"] = digest(result)
    destination = cache_path(out, job["cache_key"])
    atomic_json(destination, result)
    job.update(status="done", code="OK", message=None, result_file=str(destination),
               document_type=analysis["document_type"], needs_review=analysis["needs_review"])
    if "draft" in result:
        job["needs_review"] = result["draft"]["review"]["attention_required"]


def mark_failed(job, exc):
    job.update(status="unknown" if exc.uncertain else "failed", code=exc.code, message=str(exc))


def recover(out, folder, m):
    recovered = 0
    for job in m["jobs"]:
        cached = read_cache(out, job["cache_key"], job["article"], m["spec"])
        if cached:
            job.update(status="done", code="OK", message=None,
                       result_file=str(cache_path(out, job["cache_key"])),
                       document_type=cached["analysis"]["document_type"],
                       needs_review=cached.get("draft", {}).get("review", {}).get("attention_required", cached["analysis"]["needs_review"]))
            continue
        if job["status"] not in ("running", "done"):
            continue
        if job["status"] == "done":
            job.update(status="failed", code="CACHE_MISSING", message="완료 결과가 삭제되었거나 손상되었습니다.")
            continue
        receipt = receipt_path(folder, job)
        if receipt.exists():
            try:
                finish_from_response(out, folder, m, job, read_json(receipt))
                recovered += 1
            except V6Error as exc:
                mark_failed(job, exc)
            except (ValueError, KeyError, TypeError):
                job.update(status="unknown", code="RECEIPT_CORRUPT", message="수신 응답 파일이 손상되어 결과를 확인하지 못했습니다.")
        else:
            job.update(status="unknown", code="API_RESULT_UNKNOWN", message="종료 당시 API 응답이 저장되지 않았습니다. 재시도하면 추가 사용량이 발생할 수 있습니다.")
    return recovered


def load_resume(cfg, run_id=None, retry_errors=False):
    choices = list(manifests(cfg["output_dir"]))
    if run_id:
        choices = [(p, m) for p, m in choices if m["run_id"] == run_id]
    else:
        choices = [(p, m) for p, m in choices if any(j["status"] != "done" for j in m["jobs"])]
    if not choices:
        raise V6Error("NOTHING_TO_RESUME", "재개할 미완료 실행이 없습니다. 새 글은 03_analyze_v6.bat로 분석하세요.")
    folder, m = choices[0]
    validate_manifest(m)
    done_before = sum(job["status"] == "done" for job in m["jobs"])
    recover(cfg["output_dir"], folder, m)
    recovered_now = sum(job["status"] == "done" for job in m["jobs"]) - done_before
    if retry_errors:
        for job in m["jobs"]:
            if job["status"] in ("failed", "unknown"):
                job.update(status="pending", code=None, message=None)
    save_manifest(folder, m)
    # 이번 호출의 증가분 계산용이며 manifest에는 보관하지 않습니다.
    m["_recovered_on_load"] = max(0, recovered_now)
    return folder, m


def validate_manifest(m):
    spec = m["spec"]
    validate_spec(spec)
    seen = set()
    for job in m["jobs"]:
        a = job["article"]
        identity = (a["cafe_id"], a["article_id"])
        if (job["status"] not in STATES or a["body_sha256"] != text_hash(a["body"]) or
                a["input_sha256"] != digest({k: a[k] for k in ("title", "body", "written_at")}) or
                job["cache_key"] != analysis_key(a, spec) or identity in seen):
            raise V6Error("PLAN_CHANGED", "재개 목록 또는 원문 스냅샷이 손상되었습니다.")
        if spec["schema_version"] in ("6.2", "6.3") and job.get("evidence_units") != source_units(a):
            raise V6Error("PLAN_CHANGED", "근거 번호 목록이 저장된 원문과 다릅니다.")
        seen.add(identity)


def inspect_recheck(cfg, run_id=None):
    """과거 실행 원문 그대로, 현재 분석 기준으로 선정합니다. 원본 파일 재검색 없음."""
    spec = make_spec(cfg)
    available = list(manifests(cfg["output_dir"]))
    choices = [(p, m) for p, m in available if m["jobs"] and (not run_id or m["run_id"] == run_id)]
    if not run_id:
        older = [(p, m) for p, m in choices if m["spec"]["fingerprint"] != spec["fingerprint"]]
        choices = older or choices
    if not choices:
        raise V6Error("NO_RECHECK_SOURCE", "재검증할 실행을 찾지 못했습니다. output_v6/runs의 실행 ID를 확인하세요.")
    _, original = choices[0]
    validate_manifest(original)
    articles = [deepcopy(j["article"]) for j in original["jobs"]]
    if any(a["cafe_id"] != cfg["cafe_id"] for a in articles):
        raise V6Error("PLAN_CHANGED", "재검증 실행의 카페와 현재 설정의 카페가 다릅니다.")
    if any(len(a["body"]) > spec["max_body_chars"] for a in articles):
        raise V6Error("BODY_TOO_LONG", "원래 대상 중 현재 본문 길이 한도를 넘는 글이 있습니다. 한도를 확인하세요.")
    blocked, unfinished = set(), set()
    keys = {analysis_key(a, spec) for a in articles}
    for _, manifest in available:
        for job in manifest["jobs"]:
            if job["cache_key"] in keys:
                if job["status"] in ("pending", "running"):
                    unfinished.add(manifest["run_id"])
                elif job["status"] in ("failed", "unknown"):
                    blocked.add(job["cache_key"])
    selected, cached, held = [], 0, 0
    for a in articles:
        key = analysis_key(a, spec)
        if read_cache(cfg["output_dir"], key, a, spec):
            cached += 1
        elif key in blocked:
            held += 1
        else:
            selected.append(a)
    plan = {"valid_files": len(articles), "unique_articles": len(articles), "duplicate_or_older_files": 0,
            "invalid_files": [], "missing_dirs": [], "cached_articles": cached, "held_errors": held,
            "too_long": [], "damaged_cache": 0, "available_new": len(selected), "selected": len(selected),
            "not_selected": 0, "requested": len(articles), "unfinished_run_ids": sorted(unfinished),
            "source_mode": "saved_run_snapshot", "recheck_of": original["run_id"],
            "comparison_articles": [{"article_id": a["article_id"], "input_sha256": a["input_sha256"]} for a in articles]}
    return selected, plan, spec


def start_recheck(cfg, run_id=None):
    articles, plan, spec = inspect_recheck(cfg, run_id)
    if plan["unfinished_run_ids"]:
        raise V6Error("UNFINISHED_RUN", "같은 재검증의 미처리 대상이 있습니다. 04_resume_v6.bat로 재개하세요.")
    if plan["held_errors"]:
        raise V6Error("RECHECK_HELD", "같은 수정 기준의 실패·미확인 결과가 있습니다. 결과 확인 후 07_retry_v6.bat를 사용하세요.")
    new_id = now().replace("-", "").replace(":", "").replace("T", "_").split("+")[0].replace(".", "_") + "_" + uuid4().hex[:8]
    folder = cfg["output_dir"] / "runs" / new_id
    folder.mkdir(parents=True, exist_ok=False)
    m = {"version": VERSION, "run_id": new_id, "created_at": now(), "updated_at": now(),
         "status": "ready", "spec": spec, "plan": plan, "jobs": []}
    for a in articles:
        m["jobs"].append({"cache_key": analysis_key(a, spec), "article": a, "status": "pending", "attempts": 0,
                          "code": None, "message": None, "calls": [], "result_file": None})
    save_manifest(folder, m)
    append_event(folder / "events.jsonl", "recheck_created", run_id=new_id, recheck_of=plan["recheck_of"],
                 selected=len(articles), model=spec["model"])
    return folder, m


def summarize(out, folder, m, code):
    counts = Counter(j["status"] for j in m["jobs"])
    usage = {k: 0 for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")}
    requests = unknown_usage = 0
    for job in m["jobs"]:
        requests += job["attempts"]
        received = {c["attempt"] for c in job["calls"]}
        unknown_usage += sum(1 for n in range(1, job["attempts"] + 1) if n not in received)
        for call in job["calls"]:
            for name in usage:
                usage[name] += call["usage"][name]
    remaining = counts["pending"] + counts["running"]
    incomplete = remaining + counts["failed"] + counts["unknown"]
    status = "cancelled" if code == "CANCELLED" else ("partial" if incomplete else "success")
    m["status"] = status
    details = [{k: j.get(k) for k in ("cache_key", "status", "code", "message", "attempts", "result_file", "needs_review")}
               | {"article_id": j["article"]["article_id"], "title": j["article"]["title"]} for j in m["jobs"]]
    summary = {"version": VERSION, "run_id": m["run_id"], "created_at": m["created_at"], "updated_at": now(),
               "status": status, "code": code if code != "OK" else ("PARTIAL" if incomplete else "OK"),
               "model": m["spec"]["model"], "prompt_version": m["spec"]["prompt_version"],
               "spec_fingerprint": m["spec"]["fingerprint"], "selected": len(m["jobs"]),
               "analyzed": counts["done"], "failed": counts["failed"], "unknown": counts["unknown"],
               "pending": remaining, "needs_review": sum(j.get("needs_review", False) for j in m["jobs"] if j["status"] == "done"),
               "api_attempts": requests, "usage_not_reported_attempts": unknown_usage,
               "reported_usage": usage, "plan": m["plan"], "items": details,
               "counts_scope": "이 실행 ID의 누계입니다. resume/retry도 같은 실행 ID에 누적합니다."}
    if m["spec"]["schema_version"] == "6.3":
        from .drafts import write_draft, render_review
        draft_folder = Path(out) / "drafts" / m["run_id"]
        paths = []
        for job in m["jobs"]:
            if job["status"] == "done":
                cached = read_cache(out, job["cache_key"], job["article"], m["spec"])
                if cached and "draft" in cached:
                    paths.append(write_draft(draft_folder, job["cache_key"], cached["draft"]))
        summary["review_file"] = str(render_review(draft_folder, paths))
        summary["drafts_saved"] = len(paths)
        summary["human_review_completed"] = 0
        summary["completion_meaning"] = "초안 저장 완료입니다. 직원 검토 완료나 의미 정확성 확정이 아닙니다."
    save_manifest(folder, m)
    atomic_json(folder / "summary.json", summary)
    atomic_json(Path(out) / "latest_status.json", {"time": now(), "run_id": m["run_id"], "status": status,
                                                   "code": summary["code"], "summary_file": str(folder / "summary.json")})
    append_event(folder / "events.jsonl", "invocation_finished", code=summary["code"], analyzed=counts["done"],
                 failed=counts["failed"], unknown=counts["unknown"], pending=remaining)
    return summary


def execute(cfg, folder, m, factory=None, emit=print):
    out = cfg["output_dir"]
    start_done = sum(j["status"] == "done" for j in m["jobs"]) - m.pop("_recovered_on_load", 0)
    recover(out, folder, m)
    save_manifest(folder, m)
    code, analyzer, current = "OK", None, None
    try:
        pending = [j for j in m["jobs"] if j["status"] == "pending"]
        if any(m["spec"]["schema_version"] != "6.3" or j["article"]["body"].strip() for j in pending):
            if factory is not None:
                analyzer = factory(m["spec"])
            else:
                key = load_api_key()
                if not key:
                    raise V6Error("API_KEY_MISSING", "02_setup_key_v6.bat에서 기존 API 키를 설정하고 04_resume_v6.bat를 실행하세요.", stop=True)
                analyzer = OpenAIAnalyzer(key, m["spec"])
        for index, job in enumerate(m["jobs"], 1):
            if job["status"] != "pending":
                continue
            current = job
            emit(f"[{index}/{len(m['jobs'])}] 분석 중 / 게시글 {job['article']['article_id']} / {job['article']['title']}")
            if m["spec"]["schema_version"] == "6.3" and not job["article"]["body"].strip():
                import json
                from .drafts import image_only_analysis
                raw = {"status": "completed", "_local_image_only": True, "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(image_only_analysis(), ensure_ascii=False)}]}]}
                finish_from_response(out, folder, m, job, raw)
                save_manifest(folder, m)
                append_event(folder / "events.jsonl", "image_only_draft_saved", article_id=job["article"]["article_id"], api_calls=0)
                emit("[초안 저장] 본문 텍스트 없음 / 이미지 직접 검토 / API 호출 0")
                current = None
                continue
            # API 호출 전에 대기 상태를 디스크에 기록해 강제 종료도 인식합니다.
            # Persist the exact request body (no key/headers) before counting an API attempt.
            request_file = Path(folder) / "requests" / (job["cache_key"] + "_" + str(job["attempts"] + 1) + ".json")
            atomic_json(request_file, build_request(job["article"], m["spec"]))
            job.update(status="running", attempts=job["attempts"] + 1)
            save_manifest(folder, m)
            append_event(folder / "events.jsonl", "api_started", article_id=job["article"]["article_id"], attempt=job["attempts"])
            try:
                raw = analyzer.analyze(job["article"])
                atomic_json(receipt_path(folder, job), raw)
                record_usage(job, raw)
                save_manifest(folder, m)
                finish_from_response(out, folder, m, job, raw)
                emit(f"[분석 저장] {LABELS[job['document_type']]} / 검토 필요: {job['needs_review']}")
            except V6Error as exc:
                mark_failed(job, exc)
                emit(f"[{job['status']}] {exc.code}: {exc}")
                if exc.stop:
                    code = exc.code
            save_manifest(folder, m)
            append_event(folder / "events.jsonl", "article_finished", article_id=job["article"]["article_id"],
                         status=job["status"], code=job["code"])
            current = None
            if code != "OK":
                break
    except KeyboardInterrupt:
        code = "CANCELLED"
        # receipt가 있으면 다음 재개에서 API 없이 결과를 복구합니다.
        emit("\n[중단] 현재 상태를 저장합니다. 04_resume_v6.bat로 남은 대상을 재개하세요.")
    except V6Error as exc:
        code = exc.code
        emit(f"[중단] {exc.code}: {exc}")
    except OSError:
        code = "FILE_WRITE_ERROR"
        emit("[저장 오류] 디스크 공간과 폴더 권한을 확인하세요. 저장된 응답은 다음 재개에서 복구합니다.")
    finally:
        if analyzer is not None and hasattr(analyzer, "close"):
            analyzer.close()
    summary = summarize(out, folder, m, code)
    summary["newly_analyzed_this_invocation"] = summary["analyzed"] - start_done
    atomic_json(folder / "summary.json", summary)
    return summary
