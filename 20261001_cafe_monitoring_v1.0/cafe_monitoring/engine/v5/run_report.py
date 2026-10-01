"""매 글 처리 후 실행 요약을 원자적으로 갱신합니다."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4
from collector import KST
from settings import VERSION,CONDITIONS


def atomic_json(path,value):
    path=Path(path); temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix='.partial_',suffix='.tmp',delete=False) as f:
            temporary=Path(f.name); json.dump(value,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        if temporary: temporary.unlink(missing_ok=True)


class RunReport:
    def __init__(self,cfg,mode):
        self.cfg=cfg; self.finalized=False
        self.run_id=datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid4().hex[:8]
        self.folder=cfg['output_dir']/self.run_id; self.folder.mkdir(parents=True,exist_ok=False)
        self.s={'schema_version':'5.0','collector_version':VERSION,'run_id':self.run_id,
          'started_at':datetime.now(KST).isoformat(),'finished_at':None,'status':'running','code':'RUNNING',
          'mode':mode,'keywords':cfg['keywords'],'cafe_id':cfg['cafe_id'],'search_conditions':CONDITIONS,
          'initial_count':cfg['initial_count'],'keyword_results':[],'pages':[],
          'items':[],'import_report':{},'recovery':{},'warnings':[],'coverage_complete':mode!='sync'}
        self.checkpoint(); self.event('run_started',mode=mode,keywords=cfg['keywords'])
    def checkpoint(self):
        s=self.s; items=s['items']
        s['found_unique_count']=len(s.get('discovered_articles',[]))
        s['considered_unique_count']=len(items)
        s['selected_total']=sum(len(i['matched_keywords']) for i in s.get('discovered_articles',[]))
        s['duplicate_count']=s['selected_total']-s['found_unique_count']
        for field,status in [('saved_count','saved'),('existing_count','existing'),('held_count','held'),
            ('skipped_count','skipped'),('failed_count','failed'),('unprocessed_count','pending'),('deferred_count','deferred')]:
            s[field]=sum(i['status']==status for i in items)
        s['unprocessed_count']+=sum(i['status']=='collecting' for i in items)
        s['attempted_count']=sum(bool(i.get('attempted')) for i in items)
        s['retry_attempted_count']=sum(bool(i.get('attempted')) and i.get('previous_status') in ('retry','pending') and i.get('previous_attempts',0)>0 for i in items)
        s['updated_at']=datetime.now(KST).isoformat()
        atomic_json(self.folder/'summary.json',s)
    def event(self,event,**data):
        try:
            with (self.folder/'events.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'time':datetime.now(KST).isoformat(),'run_id':self.run_id,'event':event,**data},ensure_ascii=False)+'\n')
        except OSError:
            if 'EVENT_LOG_WRITE_FAILED' not in self.s['warnings']: self.s['warnings'].append('EVENT_LOG_WRITE_FAILED')
    def finish(self,history=None,code=None,message=None):
        if self.finalized: return self.exit_code
        s=self.s
        if code:
            for i in s['items']:
                if i['status']=='collecting': i.update(status='pending',code=code)
            s.update(status='stopped',code=code,message=message)
        else:
            partial=(not s['coverage_complete'] or any(i['status'] in ('skipped','failed','pending','collecting','deferred') for i in s['items']))
            # 기존 등급 보류도 partial로 표시합니다. 기존 저장 글만 있으면 신규 0개도 success입니다.
            partial=partial or any(i['status']=='held' for i in s['items'])
            s.update(status='partial' if partial else 'success',code='PARTIAL' if partial else 'OK')
        if history:
            s['history_totals']=history.stats()
            active=history.active_rows(s['keywords'])
            s['active_queue_remaining']=sum(r['status'] in ('pending','collecting','retry') for r in active)
            s['active_manual_review_count']=sum(r['status']=='error' for r in active)
        s['finished_at']=datetime.now(KST).isoformat()
        self.checkpoint()
        self.event('run_finished',status=s['status'],code=s['code'],saved_count=s['saved_count'],existing_count=s['existing_count'])
        try:
            with (self.cfg['output_dir']/'runs.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({k:s[k] for k in ['run_id','started_at','finished_at','mode','status','code','saved_count','existing_count','skipped_count','held_count','failed_count','unprocessed_count','deferred_count']},ensure_ascii=False)+'\n')
        except OSError:
            s['warnings'].append('RUN_INDEX_WRITE_FAILED'); self.checkpoint()
        self.finalized=True
        self.exit_code=130 if code=='CANCELLED' else (1 if code else (3 if s['status']=='partial' else 0))
        print(f'\n[실행 결과: {s["status"]} / {s["code"]}] 신규 저장 {s["saved_count"]} / 기존 저장으로 접속 생략 {s["existing_count"]}')
        print(f'등급 등 보류 유지 {s["held_count"]} / 이번 건너뜀 {s["skipped_count"]} / 실패 {s["failed_count"]} / 미처리 {s["unprocessed_count"]} / 재시도 대기 {s["deferred_count"]}')
        print(f'검색 범위 확인 완료: {s["coverage_complete"]} / 요약 JSON: {self.folder/"summary.json"}')
        return self.exit_code
