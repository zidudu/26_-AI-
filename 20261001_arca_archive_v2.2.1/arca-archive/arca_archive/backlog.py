"""Stored evidence about unfinished media; this does not probe remote availability."""
from .common import now_iso

REASONS = {
    "channel_paused": "채널 자동 수집 일시중지",
    "media_disabled": "채널 미디어 수집 꺼짐",
    "source_deleted": "원문 삭제로 주소 갱신 불가",
    "access_blocked": "원문 HTTP 451 기록·재확인 대기",
    "article_pending": "본문 수집·접근 복구 필요",
    "manual_review": "자동 재시도 종료·확인 필요",
    "retry_scheduled": "재시도 시각 대기",
    "partial_saved": "부분 파일 이어받기 대기",
    "refresh_needed": "본문에서 주소 재확인 필요",
    "ready": "다음 대기 작업에서 처리 대상",
}

def inspect_backlog(db, channel_id=None, reason=None, limit=60, offset=0):
    stamp = now_iso()
    sql = """WITH backlog AS (
        SELECT m.id,m.article_id,m.kind,m.state,m.state_code,m.attempts,m.next_retry_at,m.created_at,
               a.title,a.channel_id,a.state article_state,a.state_code article_state_code,
               a.next_retry_at article_next_retry_at,c.slug channel_slug,
        CASE WHEN c.enabled=0 THEN 'channel_paused'
             WHEN c.collect_media=0 THEN 'media_disabled'
             WHEN a.state='deleted' THEN 'source_deleted'
             WHEN a.state_code='BLOCKED_451' THEN 'access_blocked'
             WHEN a.state<>'collected' THEN 'article_pending'
             WHEN m.state='error' THEN 'manual_review'
             WHEN m.next_retry_at>? THEN 'retry_scheduled'
             WHEN m.state_code='DOWNLOAD_PAUSED' THEN 'partial_saved'
             WHEN m.state='expired' OR m.url_expires_at<=? THEN 'refresh_needed'
             ELSE 'ready' END reason
        FROM media m JOIN articles a ON a.id=m.article_id JOIN channels c ON c.id=a.channel_id
        WHERE m.state IN ('pending','expired','failed','error')
        AND m.kind IN ('image','gif','video','emoticon')
        AND (? IS NULL OR a.channel_id=?)) """
    params = [stamp, stamp, channel_id, channel_id]
    conn = db.connect()
    counts = {r['reason']: r['n'] for r in conn.execute(sql + "SELECT reason,COUNT(*) n FROM backlog GROUP BY reason", params)}
    condition = " WHERE reason=?" if reason in REASONS else ""
    filtered_params = params + ([reason] if condition else [])
    total = counts.get(reason, 0) if condition else sum(counts.values())
    rows = [dict(r) for r in conn.execute(sql + "SELECT * FROM backlog" + condition + " ORDER BY created_at,id LIMIT ? OFFSET ?", [*filtered_params, limit, offset])]
    return {"at": stamp, "total": total, "all_unfinished": sum(counts.values()), "counts": counts, "rows": rows}
