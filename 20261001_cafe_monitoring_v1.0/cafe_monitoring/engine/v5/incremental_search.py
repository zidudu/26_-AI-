"""검색어별 확인 날짜와 미완료 페이지를 기억합니다. 본문 작업은 별도 영속 큐입니다."""
from datetime import datetime, timedelta
from copy import deepcopy
import time
from collector import CollectorError,KST
from batch_search import load_page,has_next
from search import select_page


def scan_keyword(page,cfg,history,keyword,timeout_error,on_page,loader=load_page,pause=time.sleep,now_fn=lambda:datetime.now(KST)):
    state=deepcopy(history.scan(keyword) or {})
    resumed=bool(state.get('cycle_started_at'))
    if not resumed:
        now=now_fn()
        last=state.get('last_completed_at')
        state.update(cycle_started_at=now.isoformat(),mode='incremental' if last else 'initial',
            lower_date=(datetime.fromisoformat(last).astimezone(KST).date()-timedelta(days=cfg['overlap_days'])).isoformat() if last else None,
            initial_count=cfg['initial_count'],next_page=1,seen_ids=[],initial_selected=0)
    # 재개할 때 마지막 확인 페이지를 겹쳐 읽습니다. 최초 페이지부터 무한 반복하지 않습니다.
    start=max(1,state['next_page']-1) if resumed else 1
    seen=set(state['seen_ids']); fingerprints=set(); last_date=None
    reached=False; reason='PAGE_LIMIT'; checked=0
    for number in range(start,start+cfg['max_pages_per_keyword_per_run']):
        if number>cfg['max_search_page']:
            reason='SEARCH_PAGE_LIMIT'; break
        if checked: pause(cfg['request_interval_seconds'])
        snapshot=loader(page,cfg,keyword,number,timeout_error)
        checked+=1
        candidates=select_page(snapshot,cfg,keyword,now_fn(),number)
        fingerprint=tuple(c.target.article_id for c in candidates)
        if fingerprint and fingerprint in fingerprints:
            raise CollectorError('PAGINATION_STALLED','같은 검색 페이지가 반복됩니다. 진행 범위를 보존하고 중단합니다.')
        fingerprints.add(fingerprint)
        accepted=[]; page_seen=set(); older=False; duplicate_rows=0
        for c in candidates:
            aid=c.target.article_id
            if aid in page_seen: duplicate_rows+=1; continue
            page_seen.add(aid)
            date=datetime.fromisoformat(c.metadata['selected_list_date'])
            if state['mode']=='incremental' and date.astimezone(KST).date().isoformat()<state['lower_date']:
                older=True; break
            if aid not in seen:
                if last_date and date>last_date:
                    raise CollectorError('SEARCH_ORDER_MISMATCH','페이지 사이 검색 순서가 바뀌었습니다. 같은 페이지부터 재확인합니다.')
                last_date=date
                if state['mode']=='initial' and state['initial_selected']>=state['initial_count']: break
                seen.add(aid)
                if state['mode']=='initial': state['initial_selected']+=1
            c.metadata.update(selection_rule='initial_n' if state['mode']=='initial' else 'since_previous_completed_scan_with_overlap')
            accepted.append(c)
            if state['mode']=='initial' and state['initial_selected']>=state['initial_count']:
                reached=True; reason='INITIAL_COUNT_REACHED'; break
        if state['mode']=='incremental' and older:
            reached=True; reason='PREVIOUS_RANGE_REACHED'
        if not reached and (not candidates or not has_next(snapshot,number)):
            reached=True; reason='RESULTS_EXHAUSTED'
        state.update(next_page=number+1,seen_ids=sorted(seen))
        if reached:
            # 해당 주기의 시작 시각만 전진. 실행 도중 새로 생긴 글은 다음 주기에서 다시 탐색합니다.
            state.update(last_completed_at=state['cycle_started_at'],cycle_started_at=None,next_page=1,seen_ids=[])
        history.save_scan_page(keyword,state,accepted)
        on_page(keyword,accepted,{'page':number,'rows':len(candidates),'accepted':len(accepted),
            'duplicates_on_page':duplicate_rows,'mode':state['mode'],'resumed':resumed,
            'lower_date':state['lower_date'],'complete':reached,'reason':reason if reached else 'CONTINUE'})
        if reached: break
    return {'keyword':keyword,'complete':reached,'reason':reason,'pages_checked':checked,
            'mode':state['mode'],'resumed':resumed,'lower_date':state['lower_date'],
            'resume_page':max(1,state['next_page']-1) if not reached else None,
            'last_completed_at':state.get('last_completed_at')}
