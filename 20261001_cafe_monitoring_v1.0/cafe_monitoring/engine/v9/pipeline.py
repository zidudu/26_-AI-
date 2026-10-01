"""Per-cafe watermarks, one export and one idempotent mail per batch.

The caller holds V8 and V754 locks. The batch record is written before state
advancement; replaying a terminal record repairs a interrupted state write.
No mail with an uncertain delivery outcome is automatically submitted again.
"""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import traceback
import uuid
import time
from html import escape

from v8.configuration import KST,V8Error,parse_time,read_json,write_json
from v8.pipeline import sha,safe_path,stamp
from v8.schedule import cutoff,explicit_window
from v9.configuration import selected, performance
from v9.history import scan,announce_history,compare_collected
from v9.ai_provider import load_settings, settings_for_record, show_settings
from v9 import __version__

# Re-export these names for existing main/parallel callers.
from v9.run_model import DONE, FINISHED, LABELS, PATTERN, key, failure, unique_items
from v9 import artifacts, mail_delivery, run_state


class Runner:
    def __init__(self,root,cfg,backend,sender):
        self.root=Path(root).resolve()
        self.cfg,self.backend,self.sender=deepcopy(cfg),backend,sender
        self.backend.performance=performance(cfg)
        self.ai_settings=load_settings(self.root)
        self.backend.ai_settings=deepcopy(self.ai_settings)
        self.out=self.root/'output_v9'
        self.state_path=self.out/'state.json'
        self.last_folder=self.last_record=None

    def load_state(self):
        return run_state.load_state(self.root, self.cfg, self.backend.words, self.state_path)

    def save(self,folder,record):
        run_state.save_record(folder, record)
        self.last_folder, self.last_record = folder, record

    def get_record(self,identifier):
        return run_state.get_record(self.out, identifier, self.backend.words)

    def new_record(self,cafes,bounds,mode,send_mail):
        identifier=('period_' if mode=='period' else 'batch_')+datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8]
        folder=self.out/('period' if mode=='period' else 'runs')/identifier
        folder.mkdir(parents=True,exist_ok=False)
        rows=[]
        for cafe in cafes:
            lo,hi=bounds[cafe['slug']]
            rows.append({**cafe,'start':lo.isoformat(),'end':hi.isoformat(),'status':'new',
                         'collection':None,'attempts':[],'count':None})
        record=dict(version='9.0.0',id=identifier,mode=mode,created_at=stamp(),status='running',stage='PREPARE',
                    keywords=sorted(set(self.backend.words)),keyword_order=list(self.backend.words),cafes=rows,artifact=None,exports=[],
                    send_mail=send_mail,mail_settings=deepcopy(self.cfg['mail']),
                    application_version=__version__, performance=performance(self.cfg),
                    ai_settings=deepcopy(self.ai_settings),
                    mail={'status':'not_started' if send_mail else 'disabled'})
        self.save(folder,record)
        return folder,record

    def execute(self,**kwargs):
        started=time.perf_counter()
        code,folder=self._execute(**kwargs)
        if folder is not None:
            elapsed=round(time.perf_counter()-started,3)
            record=self.last_record
            record.setdefault('execution_times',[]).append(dict(finished_at=stamp(),wall_seconds=elapsed,status=record['status']))
            self.save(folder,record)
            message=f'[실행 실제 경과 시간] {elapsed:.1f}초 / 이번 호출 시작부터 결과 처리까지'
            with (folder/'run.log').open('a',encoding='utf-8') as stream:
                stream.write(message+'\n')
            print(message,flush=True)
        return code,folder

    def _execute(self,*,now=None,start=None,end=None,slugs=None,send_mail=False,retry=False,identifier=None):
        now=now or datetime.now(KST)
        period=bool(start or end)
        if period and identifier: raise V8Error('ARGUMENT_CONFLICT','새 기간과 기존 실행 번호를 함께 지정할 수 없습니다.')
        state=None
        if identifier:
            folder,record=self.get_record(identifier)
            if record['mode']=='scheduled':
                state=self.load_state()
                if state.get('active')!=identifier:
                    raise V8Error('NOT_ACTIVE','완료된 실행은 다시 발송하지 않습니다. 과거 기간은 36번을 사용하세요.')
        elif period:
            lo,hi=explicit_window(start,end,now)
            cafes=selected(self.cfg,slugs)
            folder,record=self.new_record(cafes,{c['slug']:(lo,hi) for c in cafes},'period',send_mail)
        else:
            if not self.cfg['schedule']['initial_start']:
                raise V8Error('START_REQUIRED','30_setup_v9.bat에서 최초 시작 시각을 지정하세요.')
            if not self.cfg['mail']['to']:
                raise V8Error('RECIPIENT_REQUIRED','30_setup_v9.bat에서 수신자를 지정하세요.')
            state=self.load_state()
            if state.get('active'):
                folder,record=self.get_record(state['active'])
            else:
                hi=cutoff(now,self.cfg['schedule'])
                cafes,bounds=[],{}
                for c in selected(self.cfg):
                    saved=state['cafes'][c['slug']]
                    lo=datetime.fromisoformat(saved['cursor']) if saved.get('cursor') else parse_time(self.cfg['schedule']['initial_start'])
                    failed=saved.get('failed_end')
                    if lo<hi and (retry or not failed or datetime.fromisoformat(failed)<hi):
                        cafes.append(c);bounds[c['slug']]=(lo,hi)
                if not cafes:
                    print('[대기] 진행할 새 예약 구간이 없습니다. 같은 경계의 실패는 37번 재시도로 확인하세요.')
                    return 0,None
                folder,record=self.new_record(cafes,bounds,'scheduled',True)
                state['active']=record['id']
                write_json(self.state_path,state)
        self.last_folder,self.last_record=folder,record
        from contextlib import redirect_stdout,redirect_stderr
        import sys
        from v8.pipeline import Tee
        with (folder/'run.log').open('a',encoding='utf-8') as log:
            with redirect_stdout(Tee(sys.stdout,log)),redirect_stderr(Tee(sys.stderr,log)):
                print(f"[V9] {record['id']} / {len(record['cafes'])}개 카페")
                for c in record['cafes']: print(f"  {c['name']}: {c['start']} 이상 ~ {c['end']} 미만")
                if record['status'] in FINISHED and record.get('stage')=='COMPLETE' and not record.get('interrupted'):
                    self.commit(state,record)
                    self.write_overview(folder,record)
                    return self.exit_code(record),folder
                if record['mail']['status'] in ('sending','unknown'):
                    record.update(status='mail_unknown',stage='MAIL')
                    record['mail']['status']='unknown'
                    self.save(folder,record)
                    print('[발송 확인 필요] 38_resolve_mail_v9.bat에서 보낸편지함 확인 결과를 기록하세요.')
                    return 3,folder
                if record.get('interrupted') and not retry:
                    print('[재시도 대기] 37_retry_v9.bat로 중단된 실행을 이어서 처리하세요.')
                    return 1,folder
                if retry and record['status']=='mail_held':
                    record['mail_settings']['send_partial']=self.cfg['mail']['send_partial']
                try:
                    if not record.get('artifact'):
                        self.collect_all(folder,record,retry)
                        self.summarize_search(folder,record)
                        self.make_export(folder,record)
                    else:
                        self.artifact(folder,record)
                    self.send(folder,record)
                    if record['status'] not in ('mail_unknown','mail_held'):
                        record['stage']='COMPLETE'
                        record.pop('interrupted',None)
                        self.save(folder,record)
                        self.commit(state,record)
                except BaseException as exc:
                    record.update(status='cancelled' if isinstance(exc,(KeyboardInterrupt,EOFError)) else 'failed',
                                  interrupted=True,error=failure(exc))
                    (folder/'error_traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
                    self.save(folder,record)
                    print(f"[실패] {record['stage']} / {record['error']['code']} / {record['error']['message']}")
                self.write_overview(folder,record)
                print(f"[V9 결과] {record['status']} / 메일 {record['mail']['status']} / {folder/'run.json'}")
                return self.exit_code(record),folder

    def identify(self,cafe):
        path=self.out/'connections.json'
        connections=read_json(path) if path.exists() else {}
        cached=connections.get(cafe['slug'])
        if cached:
            cid=cached.get('club_id')
            if not isinstance(cid,str) or not re.fullmatch(r'[1-9][0-9]{0,11}',cid):
                raise V8Error('CONNECTION_INVALID','저장된 카페 연결 정보를 확인하세요.')
            if cafe.get('club_id') and cafe['club_id']!=cid:
                raise V8Error('CAFE_ID_MISMATCH','설정과 저장된 연결 ID가 다릅니다.')
        else:
            cid,evidence=self.backend.identify(cafe)
            if any(v.get('club_id')==cid and slug!=cafe['slug'] for slug,v in connections.items()):
                raise V8Error('DUPLICATE_CAFE_ID','다른 카페에 이미 연결된 clubId입니다.')
            connections[cafe['slug']]={'club_id':cid,'checked_at':stamp(),'evidence':evidence}
            write_json(path,connections)
        cafe['club_id']=cid

    def collect_all(self,folder,record,retry):
        self.backend.performance=performance({'performance': record.get('performance', performance(self.cfg))})
        self.backend.ai_settings=settings_for_record(record)
        from v9.main import show_performance
        show_performance({'performance': self.backend.performance})
        show_settings(self.backend.ai_settings,
                      requested_concurrency=self.backend.performance['analysis_concurrency'])
        concurrency=self.cfg.get('collection',{}).get('concurrency',2)
        if type(concurrency) is not int or concurrency not in (1,2,3):
            raise V8Error('INVALID_CONCURRENCY','동시 수집 수는 1, 2, 3 중 하나입니다.')
        todo=[c for c in record['cafes'] if not c.get('collection')]
        if not todo: return
        self.backend.check()
        record['stage']='COLLECT'
        self.save(folder,record)
        print(f'[수집 방식] 동시 수집 {concurrency}개 / '+('기존 순차 엔진' if concurrency==1 else '카페별 검색·게시글 탭 분리'),flush=True)
        began=time.perf_counter()
        try:
            if concurrency==1:
                self.collect_sequential(folder,record,retry)
            else:
                import asyncio
                from v9.parallel import collect_all
                asyncio.run(collect_all(self,folder,record,concurrency))
        finally:
            elapsed=round(time.perf_counter()-began,3)
            if concurrency==1:
                record.setdefault('collection_attempts',[]).append(dict(finished_at=stamp(),mode='sequential',concurrency=1,wall_seconds=elapsed))
            self.save(folder,record)
            print(f'[전체 수집 실제 경과 시간] {elapsed:.1f}초 / 카페별 시간 합계와 별도',flush=True)

    def collect_sequential(self,folder,record,retry):
        self.backend.check()
        record['stage']='COLLECT'
        self.save(folder,record)
        todo=[c for c in record['cafes'] if not c.get('collection')]
        if not todo: return
        with self.backend.browser():
            for cafe in todo:
                name=cafe['slug']
                print(f"\n[카페 수집] {cafe['code']} · {cafe['name']}")
                lo,hi=datetime.fromisoformat(cafe['start']),datetime.fromisoformat(cafe['end'])
                try:
                    self.identify(cafe)
                    history,prior=scan(self.root,cafe['club_id'],lo,hi,self.backend.legacy_out)
                    announce_history(history)
                    recovered=False
                    for attempt in reversed(cafe['attempts']):
                        child=safe_path(folder,attempt)
                        if not (child/'collection.json').exists(): continue
                        try:
                            items=self.backend.load_collection(cafe,child,lo,hi)
                            recovered=True
                            break
                        except Exception: continue
                    if not recovered:
                        relative='cafes/'+name+'/collect_'+uuid.uuid4().hex[:12]
                        child=safe_path(folder,relative)
                        child.mkdir(parents=True,exist_ok=False)
                        cafe['attempts'].append(relative)
                        self.save(folder,record)
                        self.backend.collect(cafe,child,lo,hi)
                        items=self.backend.load_collection(cafe,child,lo,hi)
                    unique_items(items)
                    cafe.update(collection=str(child.relative_to(folder)),status='collected',count=len(items),
                                article_keys=sorted(key(i) for i in items))
                    cafe.pop('error',None)
                    try:
                        compared=compare_collected(child,prior,history,child.parent/'history_overlap.json')
                        cafe['duplicates']=compared['previously_collected_articles']
                    except Exception as exc:
                        cafe['history_note']=type(exc).__name__
                except (KeyboardInterrupt,EOFError): raise
                except Exception as exc:
                    cafe.update(status='failed',error=failure(exc))
                    error_path=folder/'cafes'/name/'error_traceback.txt'
                    error_path.parent.mkdir(parents=True,exist_ok=True)
                    error_path.write_text(traceback.format_exc(),encoding='utf-8')
                    print(f"[카페 실패·다음 카페 진행] {name} / {cafe['error']['code']} / {cafe['error']['message']}")
                    if getattr(exc,'code','')=='REQUEST_BLOCKED':
                        # A site-wide block is never worked around by changing cafes.
                        for pending in todo[todo.index(cafe)+1:]:
                            pending.update(status='failed',error={'code':'DEFERRED_REQUEST_BLOCKED','message':'앞선 접근 제한으로 웹 요청을 중단했습니다.'})
                        self.save(folder,record)
                        break
                self.save(folder,record)

    def make_export(self,folder,record):
        items=[]
        for cafe in record['cafes']:
            if cafe.get('collection'):
                rows=self.backend.load_collection(cafe,safe_path(folder,cafe['collection']),
                    datetime.fromisoformat(cafe['start']),datetime.fromisoformat(cafe['end']))
                if set(cafe['article_keys'])!=unique_items(rows):
                    raise V8Error('COLLECTION_CHANGED','수집 후 게시글 목록이 변경되었습니다.')
                items.extend(rows)
        unique_items(items)
        record['stage']='EXPORT'
        self.save(folder,record)
        report=None
        for name in reversed(record['exports']):
            child=safe_path(folder,name)
            if (child/'summary.json').exists():
                candidate=read_json(child/'summary.json')
                if candidate.get('status') in DONE:
                    self.validate_export(candidate,child,items)
                    report=candidate
                    break
        if report is None:
            name='export_'+uuid.uuid4().hex[:12]
            child=folder/name
            child.mkdir()
            record['exports'].append(name)
            self.save(folder,record)
            self.backend.performance=performance({'performance': record.get('performance', performance(self.cfg))})
            self.backend.ai_settings=settings_for_record(record)
            report,code=self.backend.export(items,child,record['cafes'])
            if code not in (0,2): raise V8Error('EXPORT_FAILED','PPT 출력을 마치지 못했습니다.')
            self.validate_export(report,child,items)
        included=unique_items(report.get('items',[]))
        for cafe in record['cafes']:
            if not cafe.get('collection'): continue
            expected=set(cafe['article_keys'])
            represented=expected & included
            cafe['included']=len(represented)
            if not expected:
                cafe['status']='completed_empty'
            elif represented!=expected:
                cafe.update(status='failed',error={'code':'PPT_ARTICLES_MISSING','message':'일부 글이 최종 PPT에 포함되지 않았습니다. 처리 완료 시각을 유지합니다.'})
            else:
                pending=any(a.get('summary_pending') or a.get('analysis_issues') or a.get('capture_problem')
                            or a.get('metadata_warnings') for a in report['items'] if key(a) in expected)
                cafe['status']='partial' if pending else 'completed'
        failed=any(c['status']=='failed' for c in record['cafes'])
        successful=any(c['status'] in DONE for c in record['cafes'])
        outcome=('failed' if not successful else 'partial' if failed or report['status']=='partial'
                 else 'completed' if included else 'completed_empty')
        ppt=Path(report['pptx']) if report.get('pptx') else None
        record.update(outcome=outcome,status='ready',artifact={'summary':str((child/'summary.json').relative_to(folder)),
            'summary_sha256':sha(child/'summary.json'),'ppt':str(ppt.relative_to(folder)) if ppt else None,
            'ppt_sha256':sha(ppt) if ppt else None,'selected':len(items),'included':len(included),
            'slides':report.get('slides',0)})
        self.save(folder,record)

    def summarize_search(self,folder,record):
        totals={'observed_pages':0,'rechecked_pages':0,'skipped_rechecks':0}
        for cafe in record['cafes']:
            ref=cafe.get('collection') or (cafe['attempts'][-1] if cafe.get('attempts') else None)
            if ref is None: continue
            path=safe_path(folder,ref)/'search_audit.json'
            if not path.exists(): continue
            audit=read_json(path)
            for row in audit.get('keyword_results',[]):
                totals['observed_pages']+=len(row.get('pages',[]))
                totals['rechecked_pages']+=row.get('rechecked_pages',0)
                totals['skipped_rechecks']+=row.get('skipped_rechecks',0)
        record['search_recheck_summary']=totals
        self.save(folder,record)
        print(f"[검색 확인 집계] 최초 목록 {totals['observed_pages']}페이지 / 재확인 {totals['rechecked_pages']}페이지 / 재접속 생략 {totals['skipped_rechecks']}페이지",flush=True)

    def validate_export(self,report,folder,source_items):
        return artifacts.validate_export(report, folder, source_items)

    def artifact(self,folder,record):
        return artifacts.read_artifact(folder, record)

    def send(self,folder,record):
        return mail_delivery.deliver(folder, record, self.sender, self.save)

    def commit(self,state,record):
        return run_state.commit_state(self.state_path, state, record)

    def resolve_mail(self,identifier,decision):
        folder, record = self.get_record(identifier)
        return mail_delivery.resolve_delivery(folder, record, decision, self.save)

    def summary_text(self,record):
        return mail_delivery.summary_text(record)

    def write_overview(self,folder,record):
        text=self.summary_text(record)+'\n메일: '+record['mail']['status']+'\n'
        (folder/'result.txt').write_text(text,encoding='utf-8-sig')
        html='<!doctype html><meta charset="utf-8"><title>V9 결과</title><style>body{font:16px/1.7 sans-serif;margin:32px}pre{white-space:pre-wrap}</style><h1>V9 카페별 결과</h1><pre>'+escape(text)+'</pre>'
        if record.get('artifact') and record['artifact'].get('ppt'):
            html+='<p><a href="'+escape(record['artifact']['ppt'],quote=True)+'">통합 PPT 열기</a></p>'
        (folder/'result.html').write_text(html,encoding='utf-8')

    @staticmethod
    def exit_code(record):
        return {'completed':0,'completed_empty':0,'partial':2,'mail_held':2,'mail_unknown':3,'cancelled':130}.get(record['status'],1)
