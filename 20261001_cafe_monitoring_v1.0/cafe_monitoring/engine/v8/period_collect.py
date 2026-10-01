"""기존 V5 프로필로 기간을 수집하며 원문·캡처·진단을 같은 실행에 저장합니다."""
from contextlib import ExitStack
from html import escape
import time
from v754.core import write_json
from v8.collector import collect_article, save_article
from v754.collect.batch_search import load_page
from v754.collect.run_lock import RunLock
from v754.period_scan import scan_range
from v754.period_sources import build_item, save_collection


def write_collection_html(folder, report, audit):
    e = lambda x: escape(str(x if x is not None else ''), quote=True)
    rows = ''.join('<tr>' + ''.join(f'<td>{e(r.get(k))}</td>' for k in
                   ('article_id', 'title', 'list_date_raw', 'written_at', 'status', 'code')) + '</tr>'
                   for r in audit.get('articles', []))
    searches = ''.join(f"<li>{e(r['keyword'])}: {e(r['status'])} / {e(r.get('reason'))} / {len(r['pages'])}페이지</li>"
                       for r in audit.get('keyword_results', []))
    body = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>V7.5.4 기간 수집</title>
<style>body{{font:16px/1.6 'Malgun Gothic',sans-serif;margin:32px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border:1px solid #ccc;text-align:left}}td{{overflow-wrap:anywhere}}</style>
<h1>V7.5.4 기간 수집 결과</h1><p>상태: {e(report['status'])} / 선정 {e(report.get('selected_articles', 0))}개</p>
<p>한국시간 {e(report['window']['start'])} 이상 ~ {e(report['window']['end'])} 미만</p>
<p>제목 키워드 검색 · 전체 게시판 · 최신순 · 작성 시각 기준</p><ul>{searches}</ul>
<p>캡처 없음: {e(', '.join(report.get('missing_capture_articles', [])) or '없음')} / 캡처 확인 필요: {e(len(report.get('capture_failures', [])))}개</p>
<p>selected: 선정 / outside_period: 기간 밖 / unverified: 읽기 실패 또는 판정 불가</p>
<table><thead><tr><th>ID</th><th>제목</th><th>목록 날짜</th><th>글 내부 시각</th><th>판정</th><th>오류 코드</th></tr></thead><tbody>{rows}</tbody></table>
<p>네이버 검색 화면에서 확인 가능한 결과를 대상으로 했습니다. 검색 색인 지연·숨김·삭제 글까지 발견했다는 보장은 아닙니다. 검색 목록 재확인은 서버의 단일 시점 스냅샷 검증이 아닙니다.</p></html>'''
    (folder / 'collection.html').write_text(body, encoding='utf-8')


def collect_with_pages(cfg, settings, webcfg, folder, report, checkpoint, window, words,
                       *, search_page, article_page, timeout_error):
    report['stage'] = 'COLLECT_PERIOD'
    source_folder = folder / 'source'
    source_folder.mkdir(exist_ok=False)
    audits = {'version': '7.5.4', 'api_calls': 0, 'items': []}
    scan = {}
    items = []
    last_request = None

    def pace():
        nonlocal last_request
        now = time.monotonic()
        if last_request is not None:
            gap = webcfg['request_interval_seconds'] - (now - last_request)
            if gap > 0:
                search_page.wait_for_timeout(gap * 1000)
        last_request = time.monotonic()

    def loader(word, number):
        pace()
        return load_page(search_page, webcfg, word, number, timeout_error)

    def fetch(choice):
        pace()
        return collect_article(article_page, choice.target,
            {**webcfg, 'capture_output_dir': source_folder, 'period_window': window.record()},
            timeout_error, navigation_url=choice.navigation_url)

    def save_item(source):
        path = save_article(source_folder, source)
        item = build_item(source, path, cfg, window)
        items.append(item)
        audits['items'].append({'article_id': item['id'], 'source_file': str(path),
                               'trace': source.get('metadata_audit', {}), 'ppt_metadata': item['metadata']})
        return item

    def save():
        report['selected_articles'] = len(items)
        report['items'] = items
        report['missing_capture_articles'] = [i['id'] for i in items if not i['captures']]
        report['capture_failures'] = [{'article_id': i['id'], 'code': 'CAPTURE_REVIEW_REQUIRED',
                                      'messages': i['capture_notes']} for i in items if i['capture_problem']]
        report['range_search_complete'] = scan.get('range_search_complete', False)
        report['articles_verified_complete'] = scan.get('articles_verified_complete', False)
        report['collection_complete'] = scan.get('complete', False)
        # 작업 중 item 참조는 진단에 중복 저장하지 않습니다.
        serializable = {**scan, 'articles': [{k: v for k, v in r.items() if k != 'item'} for r in scan.get('articles', [])]}
        write_json(folder / 'search_audit.json', serializable)
        write_json(folder / 'metadata_audit.json', audits)
        save_collection(folder, report, items)
        checkpoint()

    try:
        ordered = scan_range(window, words, webcfg, settings, scan, loader=loader,
                             fetch=fetch, save_item=save_item, checkpoint=save)
        items[:] = ordered
        return items
    finally:
        save()
        write_collection_html(folder, report, scan)


def collect_with_browser(cfg, settings, webcfg, folder, report, checkpoint, window, words):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    with ExitStack() as stack:
        stack.enter_context(RunLock(webcfg['profile_lock']))
        pw = stack.enter_context(sync_playwright())
        options = {'user_data_dir': str(webcfg['profile_dir']), 'headless': False,
                   'locale': 'ko-KR', 'timezone_id': 'Asia/Seoul',
                   'viewport': {'width': 1365, 'height': 900}}
        if webcfg['browser'] != 'chromium':
            options['channel'] = webcfg['browser']
        context = pw.chromium.launch_persistent_context(**options)
        stack.callback(context.close)
        return collect_with_pages(cfg, settings, webcfg, folder, report, checkpoint, window, words,
                                  search_page=context.new_page(), article_page=context.new_page(), timeout_error=PWTimeout)
