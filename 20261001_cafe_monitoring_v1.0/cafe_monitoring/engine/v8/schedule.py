"""예약 시각이 지난 마지막 경계를 계산합니다. 모든 구간은 [시작, 종료)입니다."""
from datetime import datetime, timedelta
import hashlib
import json
from .configuration import KST, V8Error, parse_time


def cutoff(now, settings):
    now = now.astimezone(KST)
    h, m = map(int, settings["time"].split(":"))
    result = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if result > now:
        result -= timedelta(days=1)
    while settings["weekdays_only"] and result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def previous_cutoff(end, settings):
    return cutoff(end - timedelta(minutes=1), settings)


def scope(cafe_id, words):
    # 키워드 순서만 바꿔도 재발송되지 않도록 정렬한 집합으로 구분합니다.
    return {"cafe_id": str(cafe_id), "keywords": sorted(set(words))}


def run_id(identity, start, end):
    raw = json.dumps([identity, start.isoformat(), end.isoformat()], sort_keys=True, ensure_ascii=False)
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"run_{start:%Y%m%d_%H%M}_{end:%Y%m%d_%H%M}_{suffix}"


def scheduled_window(cfg, state, now):
    end = cutoff(now, cfg["schedule"])
    start = (datetime.fromisoformat(state["cursor"]) if state.get("cursor")
             else parse_time(cfg["schedule"]["initial_start"]))
    if start >= end:
        return None
    return start, end


def explicit_window(start, end, now):
    if not start or not end:
        raise V8Error("PERIOD_REQUIRED", "시작·종료 시각을 모두 지정하세요.")
    lo, hi = parse_time(start), parse_time(end)
    if lo >= hi or hi > now:
        raise V8Error("INVALID_PERIOD", "시작 < 종료 ≤ 현재 시각이어야 합니다.")
    return lo, hi
