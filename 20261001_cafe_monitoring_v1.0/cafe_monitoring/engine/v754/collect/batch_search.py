"""V3에서 검증한 페이지 탐색: 검색어 하나의 최신 N개를 선정합니다."""
from datetime import datetime
from pathlib import Path
import time

from .collector import CollectorError, KST
from .page_guard import assert_session
from .search import search_url, verify_search_url, select_page

ROOT = Path(__file__).resolve().parent


def validate_count(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise CollectorError('INVALID_COUNT', '수집 개수는 1~100 사이 정수로 입력하세요.')
    return value


def has_next(snapshot, page_no):
    if snapshot.get('pagination_present') is not True:
        raise CollectorError('PAGINATION_UNKNOWN', '페이지 이동 영역을 확인하지 못해 목록 끝으로 처리하지 않습니다.')
    numbers = snapshot.get('page_numbers', [])
    if not numbers or page_no not in numbers:
        raise CollectorError('PAGINATION_UNKNOWN', '현재 페이지 번호를 확인하지 못했습니다.')
    return page_no + 1 in numbers or snapshot.get('next_group') is True


def load_page(page, cfg, keyword, page_no, timeout_error):
    timeout_ms = cfg['timeout_seconds'] * 1000
    try:
        response = page.goto(search_url(cfg['cafe_id'], keyword, page_no),
                             wait_until='domcontentloaded', timeout=timeout_ms)
        if response and response.status in (403, 429):
            raise CollectorError('REQUEST_BLOCKED', f'검색 요청 제한(HTTP {response.status})으로 중단합니다.')
        if response and response.status >= 400:
            raise CollectorError('SEARCH_HTTP_ERROR', f'검색 페이지 HTTP {response.status} 오류입니다.')
        assert_session(page)
        verify_search_url(page.url, cfg['cafe_id'], keyword, page_no)
        extractor = (ROOT / 'search_snapshot.js').read_text(encoding='utf-8')
        ready = '(args) => { const s = (' + extractor + ')(); return ' + (
            's.query_values.length > 0 && s.query_values.every(v => v === args.keyword) '
            '&& s.scope && s.sort && s.current_page === String(args.page) '
            '&& (s.rows.length > 0 || s.empty); }')
        page.wait_for_function(ready, arg={'keyword': keyword, 'page': page_no}, timeout=timeout_ms)
        assert_session(page)
        return page.evaluate('(' + extractor + ')()')
    except timeout_error:
        assert_session(page)
        raise CollectorError('SEARCH_NOT_READY', f'검색 {page_no}페이지 로딩 시간이 초과됐습니다.') from None


def find_many(page, cfg, keyword, count, timeout_error, on_page, loader=load_page, pause=time.sleep):
    """모든 대상 확정 후 수집. 실패한 글을 더 오래된 글로 보충하지 않습니다."""
    validate_count(count)
    selected, seen, fingerprints = [], set(), set()
    last_date = None
    # 목록이 실행 도중 갱신될 수 있으므로 시작 시점의 단일 스냅샷이라고 주장하지 않습니다.
    for page_no in range(1, cfg['max_pages'] + 1):
        if page_no > 1:
            pause(cfg['request_interval_seconds'])
        snapshot = loader(page, cfg, keyword, page_no, timeout_error)
        now = datetime.now(KST)
        candidates = select_page(snapshot, cfg, keyword, now, page_no)
        fingerprint = tuple(s.target.article_id for s in candidates)
        if candidates and fingerprint in fingerprints:
            raise CollectorError('PAGINATION_STALLED', '이전과 같은 검색 목록이 반복되어 중단합니다.')
        fingerprints.add(fingerprint)
        duplicates = 0
        for choice in candidates:
            key = (choice.target.cafe_id, choice.target.article_id)
            if key in seen:
                duplicates += 1
                continue
            date = datetime.fromisoformat(choice.metadata['selected_list_date'])
            if last_date and date > last_date:
                raise CollectorError('SEARCH_ORDER_MISMATCH', '페이지 사이 작성일 순서가 맞지 않습니다. 검색 목록이 갱신됐을 수 있습니다.')
            last_date = date
            seen.add(key)
            if len(selected) < count:
                choice.metadata.update(result_rank=len(selected) + 1,
                    selection_rule='first_n_unique_articles_in_latest_search_results')
                selected.append(choice)
        enough = len(selected) == count
        more = False if enough or not candidates else has_next(snapshot, page_no)
        on_page(selected, {'page': page_no, 'rows': len(candidates),
                          'duplicates_skipped': duplicates, 'selected_so_far': len(selected),
                          'checked_at': now.isoformat()})
        if enough:
            return selected, 'REQUEST_REACHED'
        if not candidates or not more:
            return selected, 'RESULTS_EXHAUSTED'
    return selected, 'PAGE_LIMIT'
