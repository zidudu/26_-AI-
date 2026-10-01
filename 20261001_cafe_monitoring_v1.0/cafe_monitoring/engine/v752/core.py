"""네트워크 호출 없이 V6 초안과 캡처 경로를 검증하고 슬라이드를 계획합니다."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import unicodedata

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# V6 prompts.analysis_key가 생성하는 카페ID_게시글ID_SHA256 파일명입니다.
# 탐색과 실제 읽기에서 동일한 조건을 사용합니다.
DRAFT_FILENAME = re.compile(
    r"(?P<cafe_id>[1-9][0-9]*)_(?P<article_id>[1-9][0-9]*)_[a-f0-9]{64}\.json"
)
DEFAULTS = {
    "v6_output_dir": "output_v6",
    "output_dir": "output_v752",
    "font_name": "맑은 고딕",
    "capture_columns": 2,
    "capture_fit_min_scale": 0.85,
    "capture_overlap_points": 14,
    "max_slides": 300,
    "cafe_names": {},
    "logo_path": "",
}
LABELS = {
    "repair_review": "수리·해결 후기", "advertisement": "광고·홍보",
    "question": "질문", "unclear": "분류 확인 필요",
}


class V7Error(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise V7Error("JSON_READ_ERROR", f"JSON을 읽지 못했습니다: {path}") from exc


def write_json(path, data):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def resolved(root, value):
    p = Path(value)
    return (p if p.is_absolute() else root / p).resolve()


def config(root):
    cfg = dict(DEFAULTS)
    # V6의 사용자 지정 출력 폴더도 자동으로 반영합니다.
    old = root / "config_v6.json"
    if old.is_file():
        v6 = read_json(old)
        cfg["v6_output_dir"] = v6.get("output_dir", "output_v6")
    # 기존 표시 설정은 이어받되 출력은 항상 별도의 output_v752입니다.
    for filename in ("config_v7.json", "config_v751.json"):
        legacy = root / filename
        if legacy.is_file():
            values = read_json(legacy)
            if not isinstance(values, dict):
                raise V7Error("CONFIG_ERROR", f"{filename}은 JSON 객체여야 합니다.")
            keys = ("v6_output_dir", "font_name", "cafe_names", "logo_path")
            if filename == 'config_v751.json':
                keys += ('capture_columns',)
            for key in keys:
                if key in values:
                    cfg[key] = values[key]
    custom = root / "config_v752.json"
    if custom.is_file():
        values = read_json(custom)
        if not isinstance(values, dict):
            raise V7Error("CONFIG_ERROR", "config_v752.json은 JSON 객체여야 합니다.")
        unknown = set(values) - set(DEFAULTS)
        if unknown:
            raise V7Error("CONFIG_ERROR", "알 수 없는 설정: " + ", ".join(sorted(unknown)))
        cfg.update(values)
    if type(cfg["capture_columns"]) is not int or cfg["capture_columns"] not in (1, 2, 3):
        raise V7Error("CONFIG_ERROR", "capture_columns는 1, 2, 3 중 하나입니다.")
    fit = cfg["capture_fit_min_scale"]
    if type(fit) not in (int, float) or not 0.85 <= fit <= 1.0:
        raise V7Error("CONFIG_ERROR", "capture_fit_min_scale은 0.85~1.0입니다.")
    overlap = cfg["capture_overlap_points"]
    if type(overlap) not in (int, float) or not 0 <= overlap <= 40:
        raise V7Error("CONFIG_ERROR", "capture_overlap_points는 0~40입니다.")
    if type(cfg["max_slides"]) is not int or not 1 <= cfg["max_slides"] <= 1000:
        raise V7Error("CONFIG_ERROR", "max_slides는 1~1000입니다.")
    if not isinstance(cfg["cafe_names"], dict):
        raise V7Error("CONFIG_ERROR", "cafe_names는 카페 ID와 이름의 JSON 객체입니다.")
    if any(not str(k).isdigit() or not isinstance(v, str) or not v.strip() or len(v) > 200
           for k, v in cfg["cafe_names"].items()):
        raise V7Error("CONFIG_ERROR", "cafe_names에는 카페 ID별 실제 이름 문자열을 지정하세요.")
    for key in ("font_name", "v6_output_dir", "output_dir", "logo_path"):
        if not isinstance(cfg[key], str) or (key != "logo_path" and not cfg[key].strip()):
            raise V7Error("CONFIG_ERROR", f"{key} 설정을 확인하세요.")
    cfg["v6_root"] = resolved(root, cfg["v6_output_dir"])
    cfg["out_root"] = resolved(root, cfg["output_dir"])
    if cfg["out_root"] == cfg["v6_root"] or cfg["v6_root"] in cfg["out_root"].parents:
        raise V7Error("CONFIG_ERROR", "V7.5.2 출력은 V6 출력 폴더 밖으로 지정하세요.")
    return cfg


def run_choices(cfg):
    base = cfg["v6_root"] / "drafts"
    return sorted([p for p in base.glob("*") if p.is_dir() and
                   any(f.is_file() and DRAFT_FILENAME.fullmatch(f.name) for f in p.glob("*.json"))],
                  key=lambda p: p.name, reverse=True)


def allowed_draft_names(folder, v6_root):
    """현재 실행 목록을 사용해 폴더에 남은 과거 JSON이 섞이지 않게 합니다."""
    converted = folder / "summary.json"
    if converted.is_file():
        report = read_json(converted)
        if "items" in report and "saved" in report:
            if report.get("run_id") != folder.name:
                raise V7Error("RUN_MISMATCH", "변환 요약의 실행 ID가 폴더와 다릅니다.")
            return {i["file"] for i in report["items"] if i.get("status") == "draft_saved"}, "converted_summary"
    manifest = v6_root / "runs" / folder.name / "manifest.json"
    if manifest.is_file():
        m = read_json(manifest)
        if m.get("run_id") != folder.name or not isinstance(m.get("jobs"), list):
            raise V7Error("RUN_MISMATCH", "V6 실행 목록을 확인할 수 없습니다.")
        return {j["cache_key"] + ".json" for j in m["jobs"] if j.get("status") == "done"}, "run_manifest"
    raise V7Error("NO_RUN_RECORD", "V6 실행 목록이 없습니다. output_v6의 runs와 drafts를 함께 보존하세요.")


def validate_draft(data):
    if not isinstance(data, dict) or data.get("draft_version") != "monitoring-1":
        raise V7Error("DRAFT_FORMAT", "지원하지 않는 V6 초안 형식입니다.")
    checksum = data.get("artifact_sha256")
    if checksum != digest({k: v for k, v in data.items() if k != "artifact_sha256"}):
        raise V7Error("DRAFT_HASH_MISMATCH", "V6 초안 내용이 저장 당시와 다릅니다.")
    a = data.get("source", {})
    m = data.get("monitoring", {})
    for field in ("cafe_id", "article_id"):
        if not isinstance(a.get(field), str) or not a[field].isdigit():
            raise V7Error("DRAFT_FORMAT", f"{field}를 확인할 수 없습니다.")
    for field in ("title", "body", "written_at", "url", "source_file"):
        if not isinstance(a.get(field), str):
            raise V7Error("DRAFT_FORMAT", f"원문의 {field}가 문자열이 아닙니다.")
    if a.get("input_sha256") != digest({k: a[k] for k in ("title", "body", "written_at")}):
        raise V7Error("SOURCE_HASH_MISMATCH", "원문 입력의 일치 여부를 확인하지 못했습니다.")
    if not a["url"].startswith("https://cafe.naver.com/"):
        raise V7Error("SOURCE_URL", "지원하지 않는 원문 링크입니다.")
    if not isinstance(m.get("display_text"), str) or type(m.get("summary_suppressed")) is not bool:
        raise V7Error("DRAFT_FORMAT", "V6 표시용 요약 정보가 없습니다.")
    if not isinstance(m.get("vehicle"), dict):
        raise V7Error("DRAFT_FORMAT", "V6 차량 정보 형식을 확인하세요.")


def capture_files(draft, root):
    a = draft["source"]
    capture = draft.get("capture") or {}
    notes, result = [], []
    source_path = resolved(root, a["source_file"])
    base = source_path.parent
    if capture.get("status") == "source_mismatch":
        return [], ["원문과 다른 캡처로 표시되어 삽입하지 않았습니다."]
    if capture.get("status") not in ("captured", "not_collected", None):
        notes.append("V5에서 캡처가 일부만 저장되었거나 확인이 필요한 상태입니다.")
    recorded_body_hash = capture.get("source_body_sha256")
    if recorded_body_hash and recorded_body_hash != hashlib.sha256(a["body"].encode()).hexdigest():
        return [], ["캡처의 본문 해시가 일치하지 않아 삽입하지 않았습니다."]
    seen = set()
    for i, item in enumerate(capture.get("files", []), 1):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            notes.append(f"캡처 {i}: 경로 형식 오류")
            continue
        raw = item["path"]
        rel = Path(raw)
        # Windows 드라이브·UNC 경로 및 상위 폴더 탈출을 차단합니다.
        path = (base / rel).resolve()
        if rel.is_absolute() or PureWindowsPath(raw).drive or base not in path.parents or path.suffix.lower() != ".png":
            notes.append(f"캡처 {i}: 허용되지 않은 경로")
            continue
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            with path.open("rb") as f:
                header = f.read(96)
            if b"DRMONE" in header[:16]:
                # 보호 파일의 복호화·변환을 수행하지 않습니다. Office에 경로를 전달합니다.
                verification = "drm_protected_original_hash_unverified"
                notes.append(f"캡처 {i}: DRM 보호 파일. 원본 PNG 해시 비교는 불가하며 PowerPoint 표시 확인이 필요합니다.")
            elif header.startswith(PNG_SIGNATURE):
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != item.get("sha256"):
                    notes.append(f"캡처 {i}: 원본 PNG 해시 불일치로 제외")
                    continue
                verification = "sha256_match"
            else:
                notes.append(f"캡처 {i}: PNG 또는 지원되는 DRM 표식을 확인하지 못해 제외")
                continue
        except OSError:
            notes.append(f"캡처 {i}: 파일 없음 또는 읽기 실패")
            continue
        result.append({"path": str(path), "index": item.get("index", i),
                       "width": item.get("width"), "height": item.get("height"),
                       "verification": verification, "original_sha256": item.get("sha256")})
    return result, notes


def field(value):
    return str(value).strip() if value is not None and str(value).strip() else "-"


def count_text(value):
    return str(value) if type(value) is int and value >= 0 else "-"


def load_articles(folder, cfg, root):
    names, selection_basis = allowed_draft_names(folder, cfg["v6_root"])
    articles, errors, seen = [], [], set()
    for name in sorted(names):
        match = DRAFT_FILENAME.fullmatch(name) if isinstance(name, str) else None
        if match is None:
            errors.append({"file": name, "code": "DRAFT_FILENAME"})
            continue
        path = folder / name
        try:
            d = read_json(path)
            validate_draft(d)
            a, m = d["source"], d["monitoring"]
            if (match.group("cafe_id"), match.group("article_id")) != (a["cafe_id"], a["article_id"]):
                raise V7Error("DRAFT_ID_MISMATCH", "파일명의 카페·게시글 ID가 초안 원문과 다릅니다.")
            identity = (a["cafe_id"], a["article_id"])
            if identity in seen:
                raise V7Error("DUPLICATE_ARTICLE", "같은 실행에 동일 게시글의 초안이 여러 개 있습니다.")
            seen.add(identity)
            images, image_notes = capture_files(d, root)
            meta = a.get("metadata") or {}
            vehicle = m["vehicle"]
            specs = [f"{label}: {vehicle[k]}" for k, label in (("model_year", "연식"), ("mileage", "주행거리")) if vehicle.get(k)]
            source_mode = m["summary_suppressed"] or m.get("display_basis") == "source_body"
            # 표시용 필드만 사용합니다. ai_candidate의 제외된 요약을 되살리지 않습니다.
            display = m["display_text"]
            if source_mode:
                display = a["body"] or display
            articles.append({
                "id": a["article_id"], "cafe_id": a["cafe_id"], "key": name[:-5],
                "title": a["title"], "url": a["url"], "written_at": a["written_at"],
                "collected_at": a.get("collected_at", "-"),
                "cafe_name": field(meta.get("cafe_name") or cfg["cafe_names"].get(a["cafe_id"]) or f"카페 {a['cafe_id']}"),
                "vehicle": field(vehicle.get("model")), "specs": " / ".join(specs) or "-",
                "complaint": "-", "same_count": "-", "views": count_text(meta.get("view_count")),
                "comments": count_text(meta.get("comment_count")), "display_text": display,
                "source_mode": source_mode, "document_type": m.get("document_type", "unclear"),
                "review_notes": list((d.get("review") or {}).get("notes", [])),
                "capture_notes": image_notes, "captures": images, "draft_path": str(path),
                "capture_problem": not images or any("DRM 보호 파일" not in n for n in image_notes),
                "draft_sha256": d["artifact_sha256"], "provenance": d.get("provenance", {}),
                "review_status": "not_reviewed",
            })
        except (V7Error, ValueError, KeyError, TypeError) as exc:
            errors.append({"file": name, "code": getattr(exc, "code", "DRAFT_FORMAT"), "message": str(exc)})
    articles.sort(key=lambda a: (a["written_at"], int(a["id"])), reverse=True)
    return articles, errors, selection_basis


def text_units(char):
    if unicodedata.combining(char):
        return 0
    return 1.0 if unicodedata.east_asian_width(char) in ("W", "F") else 0.6


def wrap_text(text, units):
    """문장을 삭제하거나 요약하지 않고 화면 표시용 줄만 나눕니다."""
    lines = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line, width = "", 0.0
        for char in paragraph:
            n = text_units(char)
            if line and width + n > units:
                lines.append(line)
                line, width = "", 0.0
            line += char
            width += n
        lines.append(line)
    return lines


def slices(width, height, box_width, box_height, overlap):
    """화면상의 포인트 단위로 전체 이미지를 연속 분할합니다. 원본 파일은 그대로 둡니다."""
    if not all(math.isfinite(float(n)) and n > 0 for n in (width, height, box_width, box_height)):
        raise V7Error("IMAGE_DIMENSIONS", "캡처 이미지 크기가 올바르지 않습니다.")
    if not 0 <= overlap < box_height:
        raise V7Error("IMAGE_DIMENSIONS", "캡처 겹침 크기가 올바르지 않습니다.")
    full_height = box_width * height / width
    if full_height / (box_height - overlap) > 1000:
        raise V7Error("IMAGE_TOO_LONG", "한 이미지에 필요한 슬라이드 수가 너무 많습니다.")
    result, start = [], 0.0
    while start < full_height - 0.01:
        visible = min(box_height, full_height - start)
        result.append({"start": start, "height": visible, "full_height": full_height,
                       "width": box_width, "start_fraction": start / full_height,
                       "end_fraction": (start + visible) / full_height})
        if start + visible >= full_height - 0.01:
            break
        start += box_height - overlap
    return result
