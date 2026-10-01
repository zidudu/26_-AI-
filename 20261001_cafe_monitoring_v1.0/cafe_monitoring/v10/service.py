"""Application commands, single-worker supervision and KST scheduling."""
from datetime import timedelta
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import uuid
from copy import deepcopy
from .common import ROOT,Problem,now,stamp,parse_time
from .database import Database
from .settings import defaults,validate,next_schedule
from .codex_auth import CodexAuth


class Service:
    def __init__(self,directory,*,launch=True):
        self.db=Database(directory);self.launch=launch
        self.lock=threading.RLock();self.child=None;self.child_run=None;self.child_log=None
        self.shutdown=threading.Event();self.secret=None;self.started_at=now()
        self.codex=CodexAuth(self.db)

    def settings(self):
        cfg=self.db.get('ui_settings') or defaults()
        cfg['catalog']=self.db.catalog()
        return cfg

    def period(self,cfg,at=None):
        at=at or now();end=at.replace(second=0,microsecond=0)
        # The collector uses minute-precision [start, end) windows. Freeze the
        # same bounds in the DB before launching, so cursors cannot skip time.
        if not cfg['stepCollect']:return {}
        if cfg['range']=='기간 직접 지정':lo=parse_time(cfg['periodStart']);end=parse_time(cfg['periodEnd'])
        else:lo=end-timedelta(hours=48 if cfg['range']=='최근 48시간' else 24)
        cursors=self.db.cursors();result={}
        for uid in cfg['selectedCafeIds']:
            start=lo
            if cfg['range']=='이전 실행 이후':
                if uid not in cursors:raise Problem('처음 수집하는 카페가 있습니다. 최근 24/48시간으로 먼저 수집하세요.')
                # Older cursors may contain seconds; round back, never forward.
                start=parse_time(cursors[uid]).replace(second=0,microsecond=0)-timedelta(days=int(cfg['overlap']))
            if start>=end:raise Problem('수집할 시간 구간이 없습니다. 시작·종료 시각을 확인하거나 다음 분에 다시 실행하세요.')
            result[uid]=(stamp(start),stamp(end))
        return result

    def create_run(self,data,request_id,*,trigger='manual',at=None):
        cfg=validate(data,for_run=True)
        if not isinstance(request_id,str) or not 1<=len(request_id)<=200:raise Problem('요청 식별자가 필요합니다.')
        with self.lock:
            with self.db.connect() as c:
                prior=c.execute('SELECT id FROM runs WHERE request_id=?',(request_id,)).fetchone()
                if prior:return self.db.run(prior[0])
            if self.child and self.child.poll() is None:raise Problem('작업 또는 로그인 브라우저를 먼저 종료하세요.',409)
            if self.codex.login_active():raise Problem('Codex 로그인이 끝난 뒤 실행하세요.',409,'CODEX_LOGIN_BUSY')
            bounds=self.period(cfg,at)
            if not cfg['stepCollect']:
                source=self.db.run(cfg['sourceRun'])
                if not source['articles']:raise Problem('원본 실행에 게시글이 없습니다.')
                if cfg['stepPpt'] and not cfg['stepAnalyze'] and not source.get('hasAnalysis'):raise Problem('저장 분석이 없는 실행입니다.')
                cfg['summaryCafes']=deepcopy(source.get('summaryCafes') or source['settings']['summaryCafes'])
                cfg['keywords']=deepcopy(source['settings']['keywords'])
                cfg['keywordIds']=deepcopy(source['settings']['keywordIds'])
            rid='v10_'+(at or now()).strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
            root=Path(cfg['outputPath']).expanduser()
            if not root.is_absolute():root=self.db.directory/root
            output=(root/rid).resolve()
            record={'output':str(output),'logPath':str(output/'run.log'),'notes':'','type':'PPT 재작성' if not cfg['stepCollect'] and not cfg['stepAnalyze'] else '예약 실행' if trigger=='schedule' else '기간 지정' if cfg['range']=='기간 직접 지정' else '정규 실행'}
            rid,created=self.db.create_run(rid,cfg,bounds,request_id=request_id,trigger=trigger,record=record)
            if created:
                try:
                    output.mkdir(parents=True,exist_ok=False)
                    if self.launch:self.spawn(rid)
                except BaseException as exc:
                    self.db.update_run(rid,state='failed',finish=True,notes='실행 시작 실패: '+str(exc));raise
            return self.db.run(rid)

    def spawn(self,rid=None):
        args=[sys.executable,'-u','-m','v10.worker','--data',str(self.db.directory)]
        args+=['--run',rid] if rid else ['--login']
        env=os.environ.copy();env['PYTHONUTF8']='1'
        if self.secret:env['OPENAI_API_KEY']=self.secret
        self.child_log=(self.db.directory/'worker_console.log').open('a',encoding='utf-8')
        try:self.child=subprocess.Popen(args,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=self.child_log,stderr=subprocess.STDOUT)
        except BaseException:self.child_log.close();raise
        self.child_run=rid

    def naver_auth(self):
        return {**self.db.get('naver_auth',{'state':'unverified','checkedAt':None}),
                'browserOpen':bool(self.child and self.child_run is None and self.child.poll() is None)}

    def login(self,confirm=False):
        if os.name!='nt':raise Problem('네이버 로그인 브라우저는 Windows에서 열 수 있습니다.')
        with self.lock:
            self.supervise()
            if self.db.active() or (self.child_run and self.child and self.child.poll() is None):
                raise Problem('진행 중인 실행을 먼저 마치세요.',409)
            auth=self.naver_auth()
            if auth['browserOpen']:return {'naverAuth':auth,'needsConfirmation':False}
            if auth['state'] in ('session_present','verified') and not confirm:
                return {'naverAuth':auth,'needsConfirmation':True}
            self.db.put('naver_login_cancelled',False)
            self.db.put('naver_auth',{'state':'unverified','checkedAt':None,'message':'전용 Chrome에서 로그인한 뒤 브라우저를 닫으세요.'})
            self.spawn();return {'naverAuth':self.naver_auth(),'needsConfirmation':False}

    def confirm_login(self):
        with self.lock:
            self.supervise();auth=self.naver_auth()
            if auth['browserOpen']:
                raise Problem('전용 Chrome 창을 먼저 닫아 주세요. V10 화면은 열어 두고 다시 눌러주세요.',409,'NAVER_BROWSER_OPEN')
            if auth['state'] not in ('session_present','verified'):
                raise Problem('저장된 로그인 세션을 확인하지 못했습니다. 네이버 로그인부터 다시 진행하세요.',409,'NAVER_LOGIN_REQUIRED')
            return {'naverAuth':auth,'message':'로그인 세션 저장과 전용 Chrome 종료를 확인했습니다.'}

    def cancel_login(self):
        with self.lock:
            auth=self.naver_auth()
            if auth['browserOpen']:self.db.put('naver_login_cancelled',True)
            return {'message':'로그인 취소를 요청했습니다. 전용 Chrome이 닫힐 때까지 잠시 기다려 주세요.' if auth['browserOpen'] else '로그인 안내를 닫았습니다.'}

    def supervise(self):
        with self.lock:
            if self.child and self.child.poll() is not None:
                if self.child_run and self.db.run_row(self.child_run)['state'] in ('queued','running','stopping'):
                    self.db.recover()
                self.child=None;self.child_run=None
                if self.child_log:self.child_log.close();self.child_log=None

    def codex_request(self,action):
        with self.lock:
            if action=='login' and (self.db.active() or (self.child and self.child.poll() is None)):
                raise Problem('현재 실행이 끝난 뒤 Codex에 로그인하세요.',409,'RUN_ACTIVE')
            return self.codex.request(action)

    def schedule_tick(self,at=None):
        at=at or now();cfg=self.db.get('ui_settings')
        if not cfg or not cfg.get('scheduleEnabled'):return
        h,m=map(int,cfg['scheduleTime'].split(':'))
        due=at.replace(hour=h,minute=m,second=0,microsecond=0)
        if at<due or (cfg['weekdaysOnly'] and at.weekday()>=5):return
        # Do not replay a missed schedule from before this server was started.
        if due<self.started_at.replace(second=0,microsecond=0):return
        marker=stamp(due)
        if self.db.get('schedule_checked')==marker:return
        self.db.put('schedule_checked',marker)
        if self.db.active() or (self.child and self.child.poll() is None) or self.codex.login_active():
            self.db.put('scheduler_message','예약 시각에 다른 작업이 실행 중이어서 건너뛰었습니다.');return
        try:
            self.create_run(cfg,'schedule:'+marker,trigger='schedule',at=due)
            self.db.put('scheduler_message','예약 실행을 시작했습니다: '+marker)
        except Exception as exc:self.db.put('scheduler_message','예약 실행 실패: '+str(exc))

    def loop(self):
        while not self.shutdown.wait(1):
            try:self.supervise();self.schedule_tick()
            except Exception as exc:self.db.put('scheduler_message',str(exc))

    def snapshot(self):
        saved=self.db.get('ui_settings');cfg=self.settings()
        return {'version':'10.0.0','settings':cfg,'settingsSaved':bool(saved),
                'settingsRevision':self.db.get('settings_revision',0),'sequence':self.db.get('sequence',0),
                'activeRun':self.db.active(),'runs':self.db.runs(),'cursors':self.db.cursors(),
                'nextRun':next_schedule(saved),'schedulerMessage':self.db.get('scheduler_message','설정을 저장하면 예약이 활성화됩니다.'),
                'naverAuth':self.naver_auth(),
                'codexAuth':self.codex.snapshot(),
                'outlookSender':self.db.get('outlook_sender',''),'counts':self.db.counts(),
                'platform':sys.platform,'legacyRoot':self.db.get('legacy_root','')}
