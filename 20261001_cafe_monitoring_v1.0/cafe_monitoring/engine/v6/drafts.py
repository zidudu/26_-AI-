"""Review drafts derived from immutable API receipts, with no automatic approval."""
from copy import deepcopy
from html import escape
import os
from pathlib import Path
import re

from .common import V6Error, atomic_json, digest, now, read_json, text_hash
from .evidence import evidence_text, resolve_evidence, source_units
from .legacy_schema import LABELS

DRAFT_VERSION = "monitoring-1"
ACTION_LABELS = {"completed": "완료", "planned": "예정", "recommended": "권유",
                 "unavailable": "불가", "unclear": "불명확"}


def fact_value(value):
    return value.get("value") if isinstance(value, dict) else value


def image_only_analysis():
    return {"document_type": "unclear", "vehicle": {"model": None, "model_year": None, "mileage": None},
            "summary": "", "symptoms": [], "parts_or_functions": [], "actions": [],
            "focus_relevance": "unclear", "evidence_ids": [], "needs_review": True,
            "review_reasons": ["본문 텍스트가 없습니다. 제목과 게시글 캡처를 직접 확인하세요. AI 이미지 분석은 수행하지 않았습니다."]}


def make_draft(article, candidate, schema_version, provenance):
    """Never silently repair AI claims. Keep the candidate and suppress flagged display fields."""
    if not isinstance(candidate, dict):
        raise V6Error("INVALID_ANALYSIS", "객체 형식의 응답이 없어 초안으로 변환할 수 없습니다.")
    candidate = deepcopy(candidate)
    summary = candidate.get("summary", "")
    if not isinstance(summary, str):
        raise V6Error("INVALID_ANALYSIS", "응답의 요약을 문자열로 읽을 수 없습니다.")
    issues, bindings = [], []
    reasons = [v for v in candidate.get("review_reasons", []) if isinstance(v, str) and v.strip()]
    units = source_units(article)
    unit_map = {u["id"]: u for u in units}
    if schema_version == "6.2":
        try:
            resolved = resolve_evidence(candidate, article)
            bindings = resolved["bindings"]
            from .schema_v62 import semantic_issues
            issues.extend(semantic_issues(candidate, article, lambda f: evidence_text(f, units)))
        except V6Error as exc:
            issues.append({"field": "summary", "message": "원문 근거 연결을 확인하지 못했습니다: " + str(exc)})
        except (KeyError, TypeError):
            reasons.append("이전 응답의 일부 상세 항목은 자동 의미 검사를 적용할 수 없어 원문 검토가 필요합니다.")
        cause = candidate.get("reported_cause")
        if cause and any(cause.get("description") == f.get("value") or
                         (cause.get("evidence_ids") and set(cause["evidence_ids"]) == set(f.get("evidence_ids", [])))
                         for f in candidate.get("diagnostic_findings", [])):
            reasons.append("이전 응답에서 점검 결과와 원인을 같은 내용으로 표시했습니다. 원인 확정 항목은 이번 초안에서 사용하지 않습니다.")
    elif schema_version == "6.3":
        ids = candidate.get("evidence_ids", [])
        bad = [i for i in ids if i not in unit_map or not unit_map[i]['text'].strip()]
        if bad:
            issues.append({"field": "summary", "message": "존재하지 않는 원문 번호가 있어 요약의 근거를 확인해야 합니다."})
        elif ids:
            bindings = [{"field": "summary", "evidence_ids": list(dict.fromkeys(ids)),
                         "quotes": [unit_map[i] for i in dict.fromkeys(ids)]}]
        elif article["body"].strip():
            reasons.append("AI가 핵심 요약의 원문 번호를 제시하지 않았습니다. 원문과 대조하세요.")
    else:
        # Older quote schemas remain available for human comparison, without pretending ID validation.
        reasons.append("이전 인용 규격의 응답입니다. 요약과 조치는 원문 대조가 필요합니다.")
    corpus = article["title"] + "\n" + article["body"]
    if re.search(r"주행\s*중|운전\s*중|달리(?:던|는)\s*중", summary) and not re.search(r"주행\s*중|운전\s*중|달리(?:던|는)\s*중", corpus):
        issues.append({"field": "summary", "message": "원문에 없는 '주행 중 발생' 표현이 있어 AI 요약 대신 원문을 표시합니다."})
    vehicle = {k: fact_value((candidate.get("vehicle") or {}).get(k)) for k in ("model", "model_year", "mileage")}
    if vehicle["model_year"] and not re.search(r"\d{2,4}\s*년\s*(?:식|형)|(?:연식|모델연도|MY)\s*[:：]?\s*\d", corpus):
        issues.append({"field": "vehicle.model_year", "message": "명시된 연식 근거를 확인하지 못해 연식 표시를 비웠습니다."})
        vehicle["model_year"] = None
    actions = []
    for action in candidate.get("actions", []):
        if isinstance(action, str):
            actions.append({"description": action, "status": "unclear"})
        elif isinstance(action, dict) and isinstance(action.get("description"), str):
            actions.append({"description": action["description"], "status": action.get("status", "unclear")})
    unavailable_booking = re.search(r"예약\s*(?:풀|불가|마감|꽉)|예약.{0,8}(?:못|안\s*됨)", corpus)
    if unavailable_booking:
        for action in actions:
            if action['status'] == 'completed' and '예약' in action['description']:
                action['status'] = 'unclear'
                issues.append({'field':'actions','message':'예약 불가 안내와 예약 완료 해석이 충돌하여 조치 상태를 불명확으로 표시합니다.'})
        if re.search(r"예약(?:을)?\s*(?:완료|했|하였|잡았|성공)", summary):
            issues.append({'field':'summary','message':'예약 불가 안내와 요약의 예약 완료 표현을 대조해야 합니다.'})
    # A known legacy action/cause contradiction must not be displayed as an unflagged action.
    if any(i["field"] == "actions/reported_cause" for i in issues):
        actions = [{**a, "status": "unclear"} for a in actions]
    for item in issues:
        reasons.append(item["message"])
    if len(article["body"].strip()) < 80:
        reasons.append("본문이 짧거나 없습니다. 캡처와 원문에서 맥락을 확인하세요.")
    capture = article.get("capture") or {"status": "not_collected", "files": []}
    if capture.get("status") != "captured":
        reasons.append("게시글 캡처가 없거나 일부만 저장됐습니다. 원문 링크로 확인하세요.")
    if (article.get("media") or {}).get("image_count", 0) or article.get("content_kind") == "image_only":
        reasons.append("이미지는 AI 입력에 포함되지 않았습니다. 캡처를 직접 확인하세요.")
    suppressed = not summary.strip() or any(i["field"] == "summary" for i in issues)
    display = article["body"] if suppressed else summary
    if not display.strip():
        display = "본문 텍스트 없음 — 제목과 캡처 확인 필요"
    return {"draft_version": DRAFT_VERSION, "created_at": now(), "source": deepcopy(article),
            "provenance": deepcopy(provenance), "ai_candidate": candidate,
            "monitoring": {"title": article["title"], "document_type": candidate.get("document_type", "unclear"),
                "vehicle": vehicle, "summary": None if suppressed else summary,
                "display_text": display, "display_basis": "source_body" if suppressed else "ai_summary",
                "summary_suppressed": suppressed,
                "symptoms": [fact_value(v) for v in candidate.get("symptoms", []) if isinstance(fact_value(v), str)],
                "parts_or_functions": [fact_value(v) for v in candidate.get("parts_or_functions", []) if isinstance(fact_value(v), str)],
                "actions": actions, "focus_relevance": candidate.get("focus_relevance", "unclear")},
            "review": {"status": "not_reviewed", "attention_required": bool(reasons or candidate.get("needs_review")),
                       "notes": list(dict.fromkeys(reasons)), "detected_issues": issues,
                       "semantic_accuracy": "not_certified", "images_analyzed_by_ai": False},
            "evidence": {"units": units, "bindings": bindings}, "capture": deepcopy(capture)}


def enrich_source(article, current):
    """Attach only captures from identical text, never replace a frozen API input."""
    result = deepcopy(article)
    for fresh in current:
        if (fresh["cafe_id"], fresh["article_id"], fresh["input_sha256"]) == (article["cafe_id"], article["article_id"], article["input_sha256"]):
            for key in ("capture", "media", "content_kind", "metadata", "source_file", "source_files"):
                if key in fresh:
                    result[key] = deepcopy(fresh[key])
            break
    return result


def write_draft(folder, key, draft):
    folder = Path(folder)
    draft = deepcopy(draft)
    draft["artifact_sha256"] = digest(draft)
    path = folder / (key + ".json")
    atomic_json(path, draft)
    return path


def render_review(folder, paths):
    """Local, escaped HTML; no scripts/network loads and no invented review approval."""
    folder = Path(folder)
    cards = []
    for path in paths:
        d = read_json(path)
        a, m, review = d["source"], d["monitoring"], d["review"]
        h = lambda s: escape(str(s if s is not None else "미수집"), quote=True)
        meta = a.get("metadata") or {}
        images = []
        for f in d["capture"].get("files", []):
            base = Path(a["source_file"]).parent.resolve()
            rel = Path(f["path"])
            img = (base / rel).resolve()
            if rel.is_absolute() or base not in img.parents:
                continue
            try:
                available = img.is_file() and text_hash_bytes(img) == f.get("sha256")
            except OSError:
                available = False
            if available:
                src = h(os.path.relpath(img, folder).replace(os.sep, "/"))
                images.append(f'<a href="{src}"><img loading="lazy" src="{src}" alt="게시글 캡처 {h(f.get("index"))}"></a>')
        reasons = "".join(f"<li>{h(note)}</li>" for note in review["notes"])
        actions = "; ".join(f'{v["description"]} ({ACTION_LABELS.get(v["status"], "불명확")})' for v in m["actions"])
        vehicle = "; ".join(f'{label}: {m["vehicle"].get(k) or "미상"}' for k, label in (("model", "차종"), ("model_year", "연식"), ("mileage", "주행거리")))
        cards.append(f'''<article><div class="badge">직원 검토 전 · {h(LABELS.get(m["document_type"], "불명확"))}</div>
<h2>{h(a["article_id"])} · {h(a["title"])}</h2>
<p><a href="{h(a["url"])}">원문 열기</a> · 작성일 {h(a["written_at"])} · 매체 {h(meta.get("cafe_name"))}</p>
<p>조회 {h(meta.get("view_count"))} · 댓글 {h(meta.get("comment_count"))} · {h(vehicle)}</p>
<h3>{"원문 표시 — AI 요약 검토 필요" if m["summary_suppressed"] else "AI 요약 초안"}</h3><p class="body">{h(m["display_text"])}</p>
<p>증상: {h("; ".join(m["symptoms"]))}<br>부품·기능: {h("; ".join(m["parts_or_functions"]))}<br>조치: {h(actions)}</p>
<ul>{reasons}</ul><details><summary>수집 본문 전체 · 원래 AI 응답과 검토 기록</summary><pre>{h(a["body"])}</pre>
<a href="{h(Path(path).name)}">초안 JSON 열기 (원래 AI 응답 포함)</a></details>
<h3>게시글 캡처 · AI 이미지 분석 미실시</h3>{"".join(images) or "<p>사용 가능한 캡처 없음</p>"}</article>''')
    html = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>동호회 모니터링 초안</title><style>body{font-family:Arial,'Malgun Gothic',sans-serif;background:#f3f5f8;color:#172638;margin:24px auto;max-width:1080px;padding:0 16px;line-height:1.65}article{background:white;padding:28px;margin:24px 0;border:1px solid #dbe1e8;border-radius:12px}h1,h2,h3{line-height:1.35}h2{font-size:22px}.badge{color:#755000}a{color:#075da8}img{display:block;max-width:100%;height:auto;margin:12px auto;border:1px solid #ddd}pre,.body{white-space:pre-wrap;overflow-wrap:anywhere}li{color:#684900}details{margin:16px 0}</style>
<h1>동호회 모니터링 초안</h1><p>저장 완료와 직원 검토 완료는 다릅니다. 아래 내용은 검토 전 초안이며, 원문·캡처와 대조해 사용하세요. 이 화면에는 검토 완료를 저장하는 기능이 없습니다.</p>'''+"".join(cards)+"</html>"
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / "monitoring.html.tmp"
    temp.write_text(html, encoding="utf-8")
    temp.replace(folder / "monitoring.html")
    return folder / "monitoring.html"


def text_hash_bytes(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def convert_saved(cfg, run_id=None):
    from .engine import manifests, validate_manifest, receipt_path
    from .api_client import decode_response
    from .source import scan_sources
    choices = [(p, m) for p, m in manifests(cfg["output_dir"]) if run_id is None or m["run_id"] == run_id]
    if not choices:
        raise V6Error("NO_SAVED_RUN", "변환할 실행 ID를 찾지 못했습니다.")
    folder, manifest = choices[0]
    validate_manifest(manifest)
    current, _ = scan_sources(cfg)
    dest = cfg["output_dir"] / "drafts" / manifest["run_id"]
    paths, items = [], []
    for job in manifest["jobs"]:
        try:
            response_path = receipt_path(folder, job)
            local_image = job['attempts'] == 0 and not job['article']['body'].strip() and job['article'].get('content_kind') == 'image_only'
            if local_image:
                raw, candidate = {}, image_only_analysis()
            else:
                raw = read_json(response_path)
                candidate = decode_response(raw)
            source = enrich_source(job["article"], current)
            draft = make_draft(source, candidate, manifest["spec"]["schema_version"], {
                "mode": "image_only_local" if local_image else "saved_response_conversion", "original_run_id": manifest["run_id"],
                "original_prompt_version": manifest["spec"]["prompt_version"], "original_schema_version": manifest["spec"]["schema_version"],
                "response_id": raw.get("id"), "response_file": None if local_image else str(response_path),
                "response_sha256": None if local_image else text_hash_bytes(response_path), "api_calls": 0,
                "note": "이전 응답의 표시 형식 변환이며 새 지시문으로 AI가 다시 분석한 결과가 아닙니다."})
            path = write_draft(dest, job["cache_key"], draft)
            paths.append(path)
            items.append({"article_id": source["article_id"], "status": "draft_saved", "file": path.name,
                          "summary_suppressed": draft["monitoring"]["summary_suppressed"], "human_review": "not_reviewed"})
        except (OSError, ValueError, V6Error) as exc:
            items.append({"article_id": job["article"]["article_id"], "status": "unavailable", "code": getattr(exc, "code", "RESPONSE_FILE_ERROR")})
    report = {"draft_version": DRAFT_VERSION, "run_id": manifest["run_id"], "api_calls": 0,
              "saved": len(paths), "unavailable": len(items)-len(paths), "human_review_completed": 0, "items": items}
    atomic_json(dest / "summary.json", report)
    return render_review(dest, paths), report
