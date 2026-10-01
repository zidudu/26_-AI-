"""V1~V5 결과를 읽고, 같은 ID에서는 마지막에 수집한 유효한 본문을 선택합니다."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from .common import V6Error, digest, read_json, text_hash


def optional_materials(data, body_hash):
    """Damaged optional assets must not discard a valid text article."""
    media = data.get('media') if isinstance(data.get('media'), dict) else {}
    media = {k: v for k, v in media.items() if k in ('image_count', 'loaded_image_count', 'video_count') and type(v) is int and v >= 0}
    metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
    metadata = {k: v for k, v in metadata.items() if k in ('cafe_name', 'view_count', 'view_count_raw', 'comment_count', 'comment_count_raw') and (v is None or type(v) in (str, int))}
    capture = data.get('capture')
    if not isinstance(capture, dict) or not isinstance(capture.get('files'), list):
        capture = {'status': 'not_collected', 'files': []}
    else:
        capture = dict(capture)
        files = [dict(f) for f in capture['files'] if isinstance(f, dict) and isinstance(f.get('path'), str)
                 and isinstance(f.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', f['sha256'])]
        if capture.get('source_body_sha256', body_hash) != body_hash:
            files = []
            capture['status'] = 'source_mismatch'
        elif len(files) != len(capture['files']):
            capture['status'] = 'partial'
        capture['files'] = files
    return media, metadata, capture


def valid_time(value):
    if not isinstance(value, str):
        raise ValueError()
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError()
    return result.timestamp()


def read_article(path, cafe_id):
    try:
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            raise V6Error("SOURCE_TOO_LARGE", "원본 JSON이 2MB를 넘습니다.")
        d = read_json(path)
        if not isinstance(d, dict) or d.get("status") != "collected":
            raise ValueError()
        cid, aid = d["cafe_id"], d["article_id"]
        if not all(isinstance(x, str) and re.fullmatch(r"[1-9][0-9]*", x) for x in (cid, aid)):
            raise ValueError()
        if cid != cafe_id:
            raise V6Error("OTHER_CAFE", "설정과 다른 카페의 글입니다.")
        title, body = d["title"], d["body"]
        if not isinstance(title, str) or not title.strip() or len(title) > 2000:
            raise ValueError()
        media = d.get("media") if isinstance(d.get("media"), dict) else {}
        image_only = (d.get("content_kind") == "image_only" and isinstance(media, dict)
                      and type(media.get("image_count")) is int and media["image_count"] > 0)
        if not isinstance(body, str) or (not body.strip() and not image_only):
            raise ValueError()
        if type(d.get("body_char_count")) is not int or d.get("body_char_count") != len(body) or d.get("body_sha256") != text_hash(body):
            raise V6Error("SOURCE_HASH_MISMATCH", "본문 길이 또는 해시가 원본 기록과 다릅니다.")
        url = urlsplit(d["url"])
        if (url.scheme != "https" or url.hostname != "cafe.naver.com" or url.username or
                url.password or url.port not in (None, 443) or
                not re.fullmatch(r"/(?:f-e/|ca-fe/)?cafes/" + cid + r"/articles/" + aid + r"/?", url.path)):
            raise ValueError()
        valid_time(d["written_at"])
        valid_time(d["collected_at"])
        keywords = d.get("matched_keywords", [])
        if not isinstance(keywords, list):
            keywords = []
        if isinstance(d.get("search"), dict):
            keywords = keywords + [d["search"].get("keyword")]
        result = {k: d[k] for k in ("cafe_id", "article_id", "title", "body", "body_sha256",
                                    "written_at", "collected_at")}
        result.update(url=f"https://cafe.naver.com/f-e/cafes/{cid}/articles/{aid}",
                      source_file=str(path.resolve()),
                      matched_keywords=sorted({s for s in keywords if isinstance(s, str) and s}),
                      source_files=[str(path.resolve())])
        media, metadata, capture = optional_materials(d, d['body_sha256'])
        result.update(content_kind=d.get("content_kind", "text"), media=media,
                      metadata=metadata, capture=capture)
        result["input_sha256"] = digest({k: result[k] for k in ("title", "body", "written_at")})
        return result
    except V6Error:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise V6Error("INVALID_SOURCE", "게시글 JSON의 ID·URL·작성일·필수 항목을 확인하세요.") from None


def scan_sources(cfg):
    candidates, invalid, missing, seen_paths = [], [], [], set()
    for folder in cfg["input_dirs"]:
        if not folder.is_dir():
            missing.append(str(folder))
            continue
        for path in sorted(folder.rglob("*.json")):
            if not re.fullmatch(r"[0-9]+_[0-9]+_.+\.json", path.name):
                continue  # summary/latest_status/분석 결과는 게시글이 아닙니다.
            if path.resolve() in seen_paths:
                continue
            seen_paths.add(path.resolve())
            try:
                candidates.append(read_article(path, cfg["cafe_id"]))
            except V6Error as exc:
                invalid.append({"file": str(path), "code": exc.code})
    latest = {}
    # 동일 ID의 수정 전/후 본문을 동시에 분석하지 않도록 수집 시각으로 선택합니다.
    for article in sorted(candidates, key=lambda a: (valid_time(a["collected_at"]), a["source_file"])):
        key = (article["cafe_id"], article["article_id"])
        previous = latest.get(key)
        if previous and previous["input_sha256"] == article["input_sha256"]:
            article["source_files"] = sorted(set(previous["source_files"] + article["source_files"]))
            article["matched_keywords"] = sorted(set(previous["matched_keywords"] + article["matched_keywords"]))
        latest[key] = article
    selected = sorted(latest.values(), key=lambda a: (valid_time(a["written_at"]), int(a["article_id"])), reverse=True)
    report = {"valid_files": len(candidates), "unique_articles": len(selected),
              "duplicate_or_older_files": len(candidates) - len(selected),
              "invalid_files": invalid, "missing_dirs": missing}
    return selected, report
