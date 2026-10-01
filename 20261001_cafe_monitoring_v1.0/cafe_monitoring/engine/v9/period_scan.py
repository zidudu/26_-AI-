"""Asynchronous port. Generated at build time; original business rules retained."""
from datetime import datetime
from v754.collect.collector import CollectorError, KST
from v754.collect.search import select_page
from v754.collect.batch_search import has_next
from v754.core import V7Error
from v754.period import timestamp
from v754.period_scan import STOP_CODES
from v9.collection_control import log as print, keyword, check_stop

def scan_range(window, words, webcfg, settings, audit, *, loader, fetch, save_item, checkpoint, now_fn=lambda: datetime.now(KST)):
    """loader(keyword,page), fetch(selection), save_item(source) 경계를 주입해 회귀 검사합니다."""
    policy = settings.get('search_recheck', 'full')
    if policy not in ('adaptive', 'full'):
        raise V7Error('INVALID_SEARCH_RECHECK', '검색 재확인 설정은 adaptive 또는 full입니다.')
    audit.update(window=window.record(), keywords=words, keyword_results=[], articles=[], duplicates=0, complete=False, search_recheck_policy=policy, skipped_rechecks=0, consistency='visible_search_pages_rechecked_not_atomic_snapshot' if policy == 'full' else 'single_page_first_observation_multi_page_rechecked_not_atomic_snapshot')
    seen, items = ({}, [])

    def read_page(word, number):
        before = now_fn()
        snapshot = loader(word, number)
        after = now_fn()
        if before.astimezone(KST).date() != after.astimezone(KST).date():
            raise V7Error('SEARCH_DATE_ROLLOVER', '검색 화면을 읽는 중 자정이 지났습니다. 같은 기간으로 다시 수집하세요.')
        choices = select_page(snapshot, webcfg, word, after, number)
        return (snapshot, choices)

    def signature(choices):
        return [(c.target.article_id, c.metadata['selected_list_date'], c.metadata['selected_title']) for c in choices]
    abort = False
    for word in words:
        keyword(word)
        check_stop()
        state = {'keyword': word, 'complete': False, 'status': 'not_started', 'pages': [], 'rechecked_pages': 0}
        audit['keyword_results'].append(state)
        if abort:
            state['reason'] = 'STOPPED_AFTER_ACCESS_ERROR'
            checkpoint()
            continue
        state['status'] = 'scanning'
        fingerprints, prior_dates, unique_ids, recorded = (set(), [], set(), [])
        try:
            reason = 'PAGE_LIMIT'
            for number in range(1, settings['max_pages_per_keyword'] + 1):
                print(f'[기간 검색] {word} / {number}페이지', flush=True)
                snapshot, choices = read_page(word, number)
                sig = signature(choices)
                fingerprint = tuple((c.target.article_id for c in choices))
                if fingerprint and fingerprint in fingerprints:
                    raise V7Error('PAGINATION_STALLED', '동일한 검색 페이지가 반복됐습니다. 전체 수집으로 처리하지 않습니다.')
                fingerprints.add(fingerprint)
                recorded.append((number, sig))
                page_record = {'page': number, 'rows': len(choices), 'ids': list(fingerprint), 'signature': sig, 'observed_at': now_fn().isoformat(), 'boundary_reached': False}
                state['pages'].append(page_record)
                checkpoint()
                older = False
                for choice in choices:
                    listed = timestamp(choice.metadata['selected_list_date'])
                    aid = choice.target.article_id
                    if aid not in unique_ids:
                        if prior_dates and listed > prior_dates[-1]:
                            raise V7Error('SEARCH_ORDER_MISMATCH', '페이지 사이 작성일 내림차순을 확인하지 못했습니다.')
                        prior_dates.append(listed)
                        unique_ids.add(aid)
                    relation = window.day_relation(listed.date())
                    if relation == 'older':
                        older = True
                        page_record['boundary_reached'] = True
                        break
                    if relation == 'newer':
                        continue
                    key = (choice.target.cafe_id, aid)
                    if key in seen:
                        row = seen[key]
                        if word not in row['matched_keywords']:
                            row['matched_keywords'].append(word)
                        if row.get('item') is not None:
                            row['item']['matched_keywords'] = list(row['matched_keywords'])
                        audit['duplicates'] += 1
                        continue
                    row = {'article_id': aid, 'cafe_id': key[0], 'url': choice.target.url, 'title': choice.metadata['selected_title'], 'list_date_raw': choice.metadata['selected_list_date_raw'], 'listed_date': listed.isoformat(), 'matched_keywords': [word], 'keyword': word, 'page': number, 'status': 'reading'}
                    audit['articles'].append(row)
                    seen[key] = row
                    checkpoint()
                    try:
                        source = fetch(choice)
                        if (source.get('cafe_id'), source.get('article_id')) != key:
                            raise V7Error('WRONG_ARTICLE', '검색 목록과 실제 읽은 게시글 ID가 다릅니다.')
                        actual = timestamp(source['written_at'])
                        if actual.date() != listed.date():
                            raise V7Error('LIST_ARTICLE_DATE_MISMATCH', '목록 날짜와 게시글 내부 작성 날짜가 다릅니다.')
                        row['written_at'] = actual.isoformat()
                        row['date_precision'] = 'as_displayed_on_article'
                        if not window.contains(source['written_at']):
                            row['status'] = 'outside_period'
                            print(f'  [기간 제외] {aid} / {actual:%Y-%m-%d %H:%M}', flush=True)
                        else:
                            item = save_item(source)
                            item['matched_keywords'] = [word]
                            row['status'] = 'selected'
                            row['source_file'] = item['refreshed_source_file']
                            row['item'] = item
                            items.append(item)
                            print(f"  [선정 {len(items)}] {aid} / {actual:%Y-%m-%d %H:%M} / 댓글 {item['comments']} / 캡처 {len(item['captures'])}장", flush=True)
                    except (CollectorError, V7Error) as exc:
                        row.update(status='unverified', code=exc.code, message=str(exc))
                        print(f'  [확인 실패] {aid} / {exc.code}', flush=True)
                        if exc.code in STOP_CODES:
                            raise
                    checkpoint()
                if older:
                    reason = 'START_DATE_PASSED'
                    break
                if not choices or not has_next(snapshot, number):
                    reason = 'RESULTS_EXHAUSTED'
                    break
                checkpoint()
            state['reason'] = reason
            if reason == 'PAGE_LIMIT':
                state['status'] = 'incomplete'
            else:
                state['status'] = 'rechecking'
                single_page_complete = len(recorded) == 1 and reason in ('START_DATE_PASSED', 'RESULTS_EXHAUSTED')
                rechecks = [] if policy == 'adaptive' and single_page_complete else recorded
                state['skipped_rechecks'] = len(recorded) - len(rechecks)
                audit['skipped_rechecks'] += state['skipped_rechecks']
                if state['skipped_rechecks']:
                    print(f'[검색 목록 저장] {word} / 한 페이지 판정 완료 / 재접속 생략', flush=True)
                for number, before in rechecks:
                    print(f'[검색 목록 재확인] {word} / {number}페이지', flush=True)
                    _, after = read_page(word, number)
                    if before != signature(after):
                        raise V7Error('SEARCH_CHANGED_DURING_SCAN', '검색 목록이 수집 도중 변경됐습니다. 같은 기간으로 다시 수집하세요.')
                    state['rechecked_pages'] += 1
                    checkpoint()
                state.update(status='completed', complete=True)
        except (CollectorError, V7Error) as exc:
            state.update(status='incomplete', reason=exc.code, message=str(exc), complete=False)
            print(f'[검색 미완료] {word} / {exc.code}', flush=True)
            abort = exc.code in STOP_CODES
        checkpoint()
    audit['range_search_complete'] = len(audit['keyword_results']) == len(words) and all((r['complete'] for r in audit['keyword_results']))
    audit['articles_verified_complete'] = all((r['status'] in ('selected', 'outside_period') for r in audit['articles']))
    audit['complete'] = audit['range_search_complete'] and audit['articles_verified_complete']
    for row in audit['articles']:
        row.pop('item', None)
    items.sort(key=lambda a: (timestamp(a['written_at']), int(a['id'])), reverse=True)
    checkpoint()
    return items
