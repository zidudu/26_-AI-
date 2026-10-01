"""검색 목록의 중복 통합 + 이전 실행의 이력 확인 + 남은 본문 수집."""
from datetime import datetime,timedelta
import time
from copy import deepcopy
import collector
from collector import CollectorError,Target,KST
from page_guard import SKIP_CODES
from incremental_search import scan_keyword
from history import valid_article

STOP_CODES={'LOGIN_REQUIRED','AUTH_REQUIRED','REQUEST_BLOCKED','BROWSER_ERROR','BROWSER_START_FAILED','FILE_ERROR','HISTORY_ERROR'}
NO_AUTO_RETRY={'WRONG_ARTICLE','WRONG_PAGE','INVALID_URL','CAFE_MISMATCH'}


def execute(run,history,page,timeout_error,scanner=scan_keyword,collect=collector.collect_article,pause=time.sleep,now_fn=lambda:datetime.now(KST)):
    cfg,s=run.cfg,run.s
    found={}; navigation={}; current_matches={}
    s["discovered_articles"]=[]
    def on_page(keyword,choices,info):
        s['pages'].append({'keyword':keyword,**info})
        for c in choices:
            found[c.target.article_id]=c
            current_matches.setdefault(c.target.article_id,set()).add(keyword)
            navigation.setdefault(c.target.article_id,c.navigation_url)
        s['discovered_articles']=[{'cafe_id':cfg['cafe_id'],'article_id':aid,'title':c.metadata['selected_title'],
            'url':c.target.url,'matched_keywords':sorted(current_matches[aid])} for aid,c in found.items()]
        run.event('search_page',keyword=keyword,**info)
        run.checkpoint()
        print(f'[검색 {keyword} / {info["page"]}페이지] 대상 {info["accepted"]} / {info["reason"]}')
    if s['mode']=='sync':
        for index,keyword in enumerate(cfg['keywords']):
            if index: pause(cfg['request_interval_seconds'])
            result=scanner(page,cfg,history,keyword,timeout_error,on_page)
            s['keyword_results'].append(result); run.checkpoint()
        s['coverage_complete']=all(r['complete'] for r in s['keyword_results'])
    # 미처리 큐의 오래된 항목부터 처리해 신규 글이 많아도 남은 작업이 계속 밀리지 않게 합니다.
    for row in history.active_rows(cfg['keywords']):
        aid=row['article_id']; status=row['status']
        if aid not in found and status=='saved': continue
        metas=history.match_list(aid)
        item={'cafe_id':row['cafe_id'],'article_id':aid,'title':row['title'],'url':row['url'],
            'matched_keywords':[m['keyword'] for m in metas],'searches':metas,
            'previous_status':status,'previous_attempts':row['attempts'],'attempted':False}
        if status=='saved': item.update(status='existing',code='ALREADY_SAVED',result_file=row['file_path'])
        elif status=='held': item.update(status='held',code=row['last_error'])
        elif status=='error': item.update(status='deferred',code='MANUAL_RETRY_REQUIRED')
        elif status=='retry' and row['next_retry_at'] and datetime.fromisoformat(row['next_retry_at'])>now_fn():
            item.update(status='deferred',code='RETRY_NOT_DUE',next_retry_at=row['next_retry_at'])
        else: item.update(status='pending',code='QUEUED')
        s['items'].append(item)
    run.checkpoint()
    print(f'[이력 확인] 기존 저장 {s["existing_count"]} / 보류 {s["held_count"]} / 수집 대기 {s["unprocessed_count"]}')
    attempts=0
    for item in s['items']:
        if item['status']!='pending': continue
        if attempts>=cfg['max_collect_per_run']:
            item['code']='COLLECTION_LIMIT'; continue
        aid=item['article_id']; row=history.get(aid)
        pause(cfg['request_interval_seconds'])
        attempts+=1
        item.update(status='collecting',attempted=True,started_at=now_fn().isoformat())
        history.set_status(aid,'collecting',attempts=row['attempts']+1)
        run.checkpoint(); run.event('article_started',article_id=aid,matched_keywords=item['matched_keywords'],previous_status=row['status'])
        print(f'[{attempts}] 수집 중 / 게시글 {aid} / {", ".join(item["matched_keywords"])}')
        start=time.monotonic(); stop=None
        try:
            target=Target(cfg['cafe_id'],aid)
            a=collect(page,target,{**cfg,'capture_output_dir':run.folder},timeout_error,navigation_url=navigation.get(aid,target.url))
            if (a.get('cafe_id'),a.get('article_id'))!=(target.cafe_id,target.article_id):
                raise CollectorError('WRONG_ARTICLE','선정한 글과 수집 결과의 ID가 다릅니다.')
            a.update(schema_version='5.0',collector_version=collector.VERSION,matched_keywords=item['matched_keywords'],
                searches=deepcopy(item['searches']),run={'run_id':run.run_id,'mode':s['mode']})
            path=collector.save_article(run.folder,a)
            # 파일 저장 후 DB를 완료 처리합니다. 그 사이 중단되면 다음 실행의 결과 등록으로 복구합니다.
            valid_article(path,cfg,(target.cafe_id,target.article_id))
            history.mark_saved(a,path)
            item.update(status='saved',code='OK',title=a['title'],result_file=path.name,body_char_count=a['body_char_count'])
            if any(m.get('selected_title') and m['selected_title']!=a['title'] for m in item['searches']):
                item['warning']='TITLE_CHANGED_SINCE_SEARCH'
            print(f'[저장 완료] {a["title"]} / {a["body_char_count"]:,}자')
            if 'capture' in a:
                item['capture_status']=a['capture']['status']
                item['capture_count']=len(a['capture']['files'])
                item['capture_warnings']=a['capture'].get('warnings',[])
                print(f'  캡처 {item["capture_count"]}장 / {item["capture_status"]}')
        except CollectorError as exc:
            item.update(code=exc.code,message=str(exc),diagnostics=exc.details)
            if exc.code in SKIP_CODES:
                history.set_status(aid,'held',last_error=exc.code,next_retry_at=None)
                item['status']='skipped'; print(f'[건너뜀] {exc.code}')
            elif exc.code in STOP_CODES:
                history.set_status(aid,'pending',last_error=exc.code)
                item['status']='pending'; stop=(exc.code,str(exc))
            else:
                failures=row['failures']+1
                state='error' if failures>=cfg['max_attempts'] or exc.code in NO_AUTO_RETRY else 'retry'
                next_time=(now_fn()+timedelta(minutes=cfg['retry_after_minutes'])).isoformat() if state=='retry' else None
                history.set_status(aid,state,failures=failures,next_retry_at=next_time,last_error=exc.code)
                item.update(status='failed',next_retry_at=next_time)
                print(f'[실패] {exc.code} / {"수동 확인 필요" if state=="error" else "다음 재시도 대기"}')
        except (OSError,ValueError):
            history.set_status(aid,'pending',last_error='FILE_ERROR')
            item.update(status='pending',code='FILE_ERROR'); stop=('FILE_ERROR','결과 파일 저장 또는 검증에 실패했습니다.')
        except BaseException:
            # SQLite 연결 오류·사용자 중단 시에도 collecting 상태가 다음 실행에서 복구됩니다.
            item['elapsed_seconds']=round(time.monotonic()-start,3)
            raise
        item.update(elapsed_seconds=round(time.monotonic()-start,3),finished_at=now_fn().isoformat())
        run.event('article_'+item['status'],article_id=aid,code=item['code'],elapsed_seconds=item['elapsed_seconds'],result_file=item.get('result_file'))
        run.checkpoint()
        if stop: return run.finish(history,*stop)
    return run.finish(history)
