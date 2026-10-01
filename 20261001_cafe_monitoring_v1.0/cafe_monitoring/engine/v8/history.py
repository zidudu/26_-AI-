"""과거 수집 기록을 읽기만 합니다. 중복 안내는 재수집을 막지 않습니다."""
from datetime import datetime
from pathlib import Path

from v754.core import digest
from v8.configuration import V8Error, read_json, write_json


def collection_paths(root, legacy_out=None):
    root = Path(root)
    paths = set()
    for base, pattern in ((root / "output_v8/runs", "*/collect_*/collection.json"),
                          (root / "output_v8_alpha", "*/collection/collection.json"),
                          (root / "output_v81_period", "*/collection/collection.json")):
        paths.update(base.glob(pattern))
    if legacy_out is not None:
        paths.update(Path(legacy_out).glob("*/collection.json"))
    return sorted(paths)


def validated_record(path):
    data = read_json(path)
    if (not isinstance(data, dict) or data.get("kind") != "period_collection" or data.get("version") != "7.5.4"
            or data.get("artifact_sha256") != digest({k: v for k, v in data.items() if k != "artifact_sha256"})):
        raise ValueError("수집 목록 형식 또는 해시 불일치")
    if not all(data.get(k) is True for k in
               ("collection_complete", "range_search_complete", "articles_verified_complete")):
        return None
    lo, hi = (datetime.fromisoformat(data["window"][key]) for key in ("start", "end"))
    if lo.tzinfo is None or hi.tzinfo is None or lo >= hi:
        raise ValueError("과거 수집 기간 오류")
    keys = set()
    for item in data["items"]:
        if (not isinstance(item, dict) or str(item["cafe_id"]) != str(data["cafe_id"])
                or not str(item["id"]).isdigit()):
            raise ValueError("게시글 식별자 오류")
        keys.add((str(item["cafe_id"]), str(item["id"])))
    return data, lo, hi, keys


def scan_history(root, cafe_id, start, end, legacy_out=None):
    """기간 교집합은 [start,end)로 비교하고 게시글은 별도 ID 집합으로 비교합니다."""
    overlaps, prior, issues = [], set(), []
    checked = incomplete = 0
    for path in collection_paths(root, legacy_out):
        try:
            result = validated_record(path)
            if result is None:
                incomplete += 1
                continue
            data, lo, hi, keys = result
            if str(data["cafe_id"]) != str(cafe_id):
                continue
            checked += 1
            # 글의 작성 시각이 수정되더라도 같은 게시글은 다시 찾을 수 있도록 전체 ID를 보관합니다.
            prior.update(keys)
            if start < hi and lo < end:
                overlaps.append({"collection": str(path), "start": lo.isoformat(),
                                 "end": hi.isoformat(), "articles": len(keys)})
        except (OSError, ValueError, TypeError, KeyError, V8Error):
            issues.append(str(path))
    return {"checked_collections": checked, "overlapping_periods": overlaps,
            "unreadable_collections": issues, "incomplete_collections_skipped": incomplete}, prior


def announce_history(history):
    count = len(history["overlapping_periods"])
    if count:
        print(f"[기간 중복 알림] 과거 수집 완료 기록 {count}개와 기간이 겹칩니다.", flush=True)
        for old in history["overlapping_periods"][:3]:
            print(f"  {old['start'][:16]} ~ {old['end'][:16]} / 당시 {old['articles']}건", flush=True)
        print("기간이 겹쳐도 다시 수집합니다. 실제 게시글 중복 수는 수집 후 표시합니다.", flush=True)
    else:
        print("[기간 중복 확인] 확인 가능한 과거 수집 완료 기록과 겹치는 기간이 없습니다.", flush=True)
    if history["unreadable_collections"]:
        print(f"[중복 확인 제한] 과거 기록 {len(history['unreadable_collections'])}개를 읽거나 검증하지 못했습니다.", flush=True)


def compare_collected(collection, prior_keys, history, output):
    result = validated_record(Path(collection) / "collection.json")
    if result is None:
        raise ValueError("현재 수집 미완료")
    data, _, _, keys = result
    duplicates = sorted(keys & prior_keys)
    record = {**history, "status": "checked", "selected_articles": len(keys),
              "previously_collected_articles": len(duplicates),
              "new_to_saved_history": len(keys - prior_keys),
              "duplicate_articles": [{"cafe_id": cafe, "article_id": aid} for cafe, aid in duplicates],
              "action": "recollect_all", "comparison": "same_cafe_and_article_id"}
    audit_path = Path(collection) / "search_audit.json"
    if audit_path.is_file():
        try:
            record["within_run_duplicates_skipped"] = int(read_json(audit_path).get("duplicates", 0))
        except (OSError, ValueError, TypeError, V8Error):
            pass
    write_json(output, record)
    print(f"[게시글 중복 알림] 이번 {len(keys)}건 중 이전 수집 글 {len(duplicates)}건 / 저장 이력에 없는 글 {len(keys - prior_keys)}건", flush=True)
    if record.get("within_run_duplicates_skipped"):
        print(f"[검색 내 중복 정리] 같은 게시글의 반복 검색 결과 {record['within_run_duplicates_skipped']}회를 건너뛰었습니다.", flush=True)
    if duplicates:
        print("중복 게시글도 이번 분석·PPT 대상에 포함합니다. 메일 발송을 선택했다면 다시 포함되어 발송될 수 있습니다.", flush=True)
    print(f"[중복 확인 기록] {output}", flush=True)
    return record
