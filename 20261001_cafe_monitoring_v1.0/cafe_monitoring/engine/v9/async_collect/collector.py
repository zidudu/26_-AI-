"""Asynchronous port. Generated at build time; original business rules retained."""
import time
from v8.collector import (CollectorError, Target, BODY, BODY_SELECTORS, body_extraction_script,
    assert_target_page, has_login_redirect, parse_target, build_article)
from .page_guard import assert_article_access
from v9.collection_control import log as print

async def wait_for_article(page, cfg):
    """본문 준비와 제한 안내를 0.25초 간격으로 함께 확인합니다. 전체 대기 예산은 1회입니다."""
    deadline = time.monotonic() + cfg['timeout_seconds']
    diagnostics = {'stage': 'article', 'frame_detected': False, 'title_visible': False, 'date_visible': False, 'body_visible': False, 'body_char_count': 0}
    extractor = body_extraction_script()
    while True:
        states = await assert_article_access(page)
        frame = page.frame(name='cafe_main')
        diagnostics['frame_detected'] = frame is not None
        state = next((s for f, s in states if f == frame), {})
        for key in ('title_visible', 'date_visible', 'body_visible', 'body_char_count'):
            diagnostics[key] = state.get(key, False if key != 'body_char_count' else 0)
        diagnostics['body_selector'] = state.get('body_selector')
        diagnostics['body_index'] = state.get('body_index')
        if frame is not None and state.get('ready') is True:
            selector = state.get('body_selector', BODY)
            index = state.get('body_index', 0)
            if selector not in BODY_SELECTORS or type(index) is not int or index < 0:
                raise CollectorError('BODY_SELECTOR_MISMATCH', '본문 검사 파일과 수집 코드가 맞지 않습니다. 수정본의 v5 폴더를 함께 덮어쓰세요.')
            raw = await frame.evaluate(extractor, {'selector': selector, 'index': index})
            if isinstance(raw, dict) and isinstance(raw.get('body'), str) and (raw['body'].strip() or (raw.get('media') or {}).get('image_count', 0) or (raw.get('media') or {}).get('video_count', 0)):
                return raw
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectorError('PAGE_NOT_READY', '본문 요소를 제한 시간 안에 확인하지 못했습니다. 원인 미확인으로 기록합니다.', diagnostics)
        await page.wait_for_timeout(min(250, remaining * 1000))

async def collect_article(page, target: Target, cfg: dict, timeout_error, navigation_url: str | None=None) -> dict:
    """지정 URL을 한 번 열고 검증된 프레임 구조에서 본문을 읽습니다."""
    timeout_ms = cfg['timeout_seconds'] * 1000
    try:
        if navigation_url and parse_target(navigation_url, target.cafe_id, cfg['cafe_slug']) != target:
            raise CollectorError('WRONG_ARTICLE', '검색 결과의 게시글 ID가 선택 대상과 다릅니다.')
        response = await page.goto(navigation_url or target.url, wait_until='domcontentloaded', timeout=timeout_ms)
        await assert_article_access(page)
        if response and response.status in (403, 429):
            raise CollectorError('REQUEST_BLOCKED', f'페이지 요청 제한(HTTP {response.status})으로 중단합니다.')
        if response and response.status >= 400:
            raise CollectorError('HTTP_ERROR', f'페이지 요청에 HTTP {response.status} 오류가 반환됐습니다.')
        if has_login_redirect(page):
            raise CollectorError('LOGIN_REQUIRED', '로그인이 필요합니다. 02_login_v5.bat 실행 후 다시 수집하세요.')
        assert_target_page(page, target, cfg['cafe_slug'])
        attempts = []
        for attempt in range(2):
            raw = await wait_for_article(page, cfg)
            assert_target_page(page, target, cfg['cafe_slug'])
            document_url = raw.get('document_url', '')
            if '/articles/' in document_url or 'articleid=' in document_url.lower():
                if parse_target(document_url, target.cafe_id, cfg['cafe_slug']) != target:
                    raise CollectorError('WRONG_ARTICLE', '내부 프레임의 게시글 ID가 요청과 다릅니다.')
            article = build_article(raw, target)
            if cfg.get('period_window') is not None:
                from v754.period import Window
                if not Window.from_record(cfg['period_window']).contains(article['written_at']):
                    article['capture'] = {'status': 'not_collected', 'reason': 'outside_period', 'files': []}
                    article['capture_attempts'] = attempts
                    return article
            from v9.async_collect.metadata import initialize_metadata
            await initialize_metadata(page, article)
            if cfg.get('capture_output_dir') is None:
                return article
            from v9.async_collect.post_capture import capture_post
            capture = await capture_post(page, raw, cfg['capture_output_dir'], article, ppi=cfg.get('ppt_image_ppi', 125), capture_columns=cfg.get('ppt_capture_columns', 2))
            article['capture'] = capture
            attempts.append({'attempt': attempt + 1, 'source_body_sha256': article['body_sha256'], 'written_at': article['written_at'], 'status': capture['status'], 'warnings': capture.get('warnings', []), 'diagnostics': capture.get('diagnostics', [])})
            article['capture_attempts'] = attempts
            retry_codes = {'CONTENT_CHANGED_BEFORE_CAPTURE', 'CONTENT_CHANGED_DURING_CAPTURE', 'CAPTURE_AREA_NOT_VISIBLE', 'CAPTURE_TIMED_OUT'}
            if attempt or capture['files'] or (not retry_codes.intersection(capture.get('warnings', []))):
                return article
            print(f'  [캡처 재확인] {target.article_id} / 원문·작성일 다시 읽기 / 마지막 1회', flush=True)
            await page.wait_for_timeout(250)
        return article
    except (timeout_error, AssertionError):
        await assert_article_access(page)
        if has_login_redirect(page):
            raise CollectorError('LOGIN_REQUIRED', '로그인이 필요합니다. 02_login_v5.bat 실행 후 다시 수집하세요.') from None
        raise CollectorError('PAGE_NOT_READY', '페이지 이동 시간이 초과됐습니다. 원인 미확인으로 기록합니다.', {'stage': 'navigation'}) from None
