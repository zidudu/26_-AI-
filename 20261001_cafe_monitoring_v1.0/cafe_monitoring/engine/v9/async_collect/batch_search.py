"""Asynchronous port. Generated at build time; original business rules retained."""
from v754.collect.batch_search import ROOT
from v754.collect.collector import CollectorError
from v754.collect.search import search_url, verify_search_url
from .page_guard import assert_session

async def load_page(page, cfg, keyword, page_no, timeout_error):
    timeout_ms = cfg['timeout_seconds'] * 1000
    try:
        response = await page.goto(search_url(cfg['cafe_id'], keyword, page_no), wait_until='domcontentloaded', timeout=timeout_ms)
        if response and response.status in (403, 429):
            raise CollectorError('REQUEST_BLOCKED', f'검색 요청 제한(HTTP {response.status})으로 중단합니다.')
        if response and response.status >= 400:
            raise CollectorError('SEARCH_HTTP_ERROR', f'검색 페이지 HTTP {response.status} 오류입니다.')
        await assert_session(page)
        verify_search_url(page.url, cfg['cafe_id'], keyword, page_no)
        extractor = (ROOT / 'search_snapshot.js').read_text(encoding='utf-8')
        ready = '(args) => { const s = (' + extractor + ')(); return ' + 's.query_values.length > 0 && s.query_values.every(v => v === args.keyword) && s.scope && s.sort && s.current_page === String(args.page) && (s.rows.length > 0 || s.empty); }'
        await page.wait_for_function(ready, arg={'keyword': keyword, 'page': page_no}, timeout=timeout_ms)
        await assert_session(page)
        return await page.evaluate('(' + extractor + ')()')
    except timeout_error:
        await assert_session(page)
        raise CollectorError('SEARCH_NOT_READY', f'검색 {page_no}페이지 로딩 시간이 초과됐습니다.') from None
