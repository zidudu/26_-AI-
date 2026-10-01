"""Adapter over the supplied V9.6.7 engine; used only inside one worker process."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import asyncio
import json
import os
import shutil
import sys
from .common import ENGINE, Problem, stamp, read_json, write_json, parse_time
from .naver_session import browser_settings,SessionStore,CollectionSession,has_session

if str(ENGINE) not in sys.path:sys.path.insert(0,str(ENGINE))


class LegacyEngine:
    def __init__(self,db,rid,cfg,folder):
        self.db,self.rid,self.ui,self.folder=db,rid,cfg,Path(folder)
        if os.name!='nt':raise Problem('실제 수집·AI·Office 연결은 Windows PC에서 실행하세요.',400,'WINDOWS_REQUIRED')
        from v9.backend import Backend
        from v9.configuration import PERFORMANCE_DEFAULTS
        from v9.ai_provider import load_settings
        from v754.core import config
        self.backend=Backend(ENGINE)
        self.legacy=Path(db.get('legacy_root','')) if db.get('legacy_root') else None
        # Configuration copies belong to this run and never overwrite V9 files.
        root=self.folder/'engine_config';root.mkdir(exist_ok=True)
        for name in ('config_v5.json','config_v6.json','config_v7.json','config_v751.json','config_v752.json',
                     'config_v753.json','config_v754.json','config_ai_v95.json','config_dashboard_v96.json'):
            source=(self.legacy/name) if self.legacy and (self.legacy/name).is_file() else ENGINE/name
            if source.is_file():shutil.copy2(source,root/name)
        self.cfg=config(root);self.cfg.update(project_root=root,out_root=self.folder,v10_raw_notes=cfg['rawNote'])
        perf={**PERFORMANCE_DEFAULTS}
        if self.legacy and (self.legacy/'config_v9.json').is_file():
            perf.update(read_json(self.legacy/'config_v9.json').get('performance',{}))
        perf['ppt_image_ppi']=int(cfg['ppi'])
        # Collection-only runs keep original screenshots and never measure them
        # in Office. The legacy capture pipeline already supports ppi=0.
        if not cfg['stepPpt']:perf['ppt_image_ppi']=0
        perf['analysis_concurrency']=max(1,min(3,int(perf.get('analysis_concurrency',2))))
        self.cfg['v93_performance']=perf
        ai=load_settings(root);ai['provider']=cfg['provider']
        if ai['provider']=='codex':ai['codex']['acknowledge_cli_differences']=True
        self.cfg['v95_ai_settings']=ai
        self.backend.cfg=self.cfg;self.backend.performance=perf;self.backend.ai_settings=ai
        self.backend.words=list(cfg['keywords'])
        from v754.period_config import load_period_config
        self.backend.base.settings=load_period_config(root)
        self.backend.webcfg=browser_settings(db,self.folder)
        self._held=ExitStack()

    def __enter__(self):
        # Share the legacy project execution lock as well as its profile lock.
        if self.legacy:
            from v754.collect.run_lock import RunLock
            self._held.enter_context(RunLock(self.legacy/'output_v8/v8.lock'))
        return self

    def __exit__(self,*args):self._held.close()

    def check(self):self.db.check_stop(self.rid)

    async def collect(self,cafes,bounds,on_cafe):
        from v9.collection_control import RequestGate,task_scope,ACCESS_CODES
        gate=RequestGate(self.backend.webcfg['request_interval_seconds'])
        normal_check=gate.check
        def check():
            self.check();normal_check()
        gate.check=check
        limit=asyncio.Semaphore(int(self.ui['parallel']))
        async def one(cafe):
            import hashlib
            uid=cafe['id'];child=self.folder/'cafes'/hashlib.sha256(uid.encode()).hexdigest()[:20];child.mkdir(parents=True,exist_ok=True)
            row=dict(code=cafe['code'],name=cafe['name'],slug=cafe['url'].rstrip('/').split('/')[-1],club_id=cafe.get('naverCafeId'))
            with task_scope(row['code'],gate):
                async with limit:
                    try:
                        self.check();gate.check()
                        self.db.log(self.rid,cafe['code']+' 수집 시작 · 카페 연결 및 키워드 검색')
                        if not row['club_id']:
                            cid,_=await self.backend.identify_async(row);row['club_id']=str(cid)
                        self.db.resolve_cafe(uid,row['club_id'])
                        lo,hi=map(parse_time,bounds[uid])
                        await self.backend.collect_async(row,child,lo,hi)
                        items=self.backend.load_collection(row,child,lo,hi)
                        from v754.period_sources import load_period_source
                        sources=[load_period_source(i) for i in items]
                        on_cafe(cafe,items,sources,None)
                        if not session.rejected:
                            self.db.put('naver_auth',{'state':'verified','checkedAt':stamp(),'message':'로그인 세션 및 카페 수집 확인'})
                    except InterruptedError:raise
                    except Exception as exc:
                        if getattr(exc,'code','') in ACCESS_CODES:gate.stop(exc.code)
                        if getattr(exc,'code','') in ('LOGIN_REQUIRED','AUTH_REQUIRED'):session.reject()
                        on_cafe(cafe,[],[],str(exc))
        async with self.backend.async_browser():
            async with CollectionSession(self.db,self.rid,self.backend.context,self.backend.webcfg,gate) as session:
                results=await asyncio.gather(*(one(c) for c in cafes),return_exceptions=True)
                if session.rejected:raise Problem('로그인 확인이 필요하여 수집을 중단했습니다. 상단 네이버 로그인에서 다시 로그인하세요.',code='LOGIN_REQUIRED')
        for r in results:
            if isinstance(r,BaseException):raise r

    def report(self,items,cafes):
        from v754.main_v754 import report_base
        report=report_base('v10_db_run')
        report.update(collection_complete=True,range_search_complete=True,articles_verified_complete=True,
            selected_articles=len(items),items=deepcopy(items),cafes=deepcopy(cafes),
            application_version='10.0.0',performance=deepcopy(self.cfg['v93_performance']),ai_settings=deepcopy(self.cfg['v95_ai_settings']))
        from v9.dashboard_data import settings,build_stats
        opts=settings(self.cfg['project_root']);opts.update(enabled=True,include_cafe_summaries=self.ui['summarySlide'])
        opts['cafe_summary_ai']=bool(opts['cafe_summary_ai'] and self.ui['summarySlide'])
        self.cfg['v96_dashboard']=opts
        # V10 passes an execution-frozen catalog, including user-added cafes.
        catalog=[(c['code'],c['name'],c['slug'],c.get('club_id')) for c in cafes]
        report['dashboard_stats']=build_stats(items,cafes,self.ui['keywords'],opts,catalog=catalog)
        report['dashboard_settings']=deepcopy(opts)
        return report

    def analysis_context(self,items,report,checkpoint):
        from v9.analysis_stage import prepare_analysis
        return prepare_analysis(items,self.cfg,self.folder,report,checkpoint)

    def render(self,items,report,checkpoint,analysis=None):
        from v9.powerpoint import render
        from v9.rebuild_saved_ppt import no_new_analysis
        render(items,self.cfg,self.folder,report,checkpoint,
            summary_resolver=analysis.summary_resolver if analysis else no_new_analysis,
            dashboard_resolver=analysis.dashboard_resolver if analysis else None)
        return Path(report['pptx'])


def login(db):
    """Launch the same persistent profile. User completes credentials in Chrome."""
    if db.get('naver_login_cancelled',False):return
    from playwright.sync_api import sync_playwright
    from v754.collect.run_lock import RunLock
    cfg=browser_settings(db);profile=cfg['profile_dir'];browser=cfg['browser']
    lock=cfg['profile_lock'];store=SessionStore(db,cfg)
    db.put('naver_auth',{'state':'unverified','checkedAt':None,'message':'로그인 브라우저가 열려 있습니다. 로그인 후 브라우저를 닫으세요.'})
    with RunLock(lock),sync_playwright() as pw:
        store.clear()
        opts=dict(user_data_dir=str(profile),headless=False,locale='ko-KR',timezone_id='Asia/Seoul')
        if browser!='chromium':opts['channel']=browser
        context=pw.chromium.launch_persistent_context(**opts)
        page=context.pages[0] if context.pages else context.new_page()
        # Cookie values are user-encrypted locally, never written to DB/logs/API.
        saved_signature=None
        try:
            if db.get('naver_login_cancelled',False):return
            page.goto('https://nid.naver.com/nidlogin.login',wait_until='domcontentloaded')
            while context.pages:
                if db.get('naver_login_cancelled',False):break
                try:
                    cookies=context.cookies()
                    if has_session(cookies):
                        from .common import digest
                        signature=digest(cookies)
                        if signature!=saved_signature:
                            store.save(cookies);saved_signature=signature
                            db.put('naver_auth',{'state':'session_present','checkedAt':stamp(),'message':'로그인 세션을 저장했습니다. 전용 Chrome을 닫은 뒤 수집을 실행하세요.'})
                    elif saved_signature is not None:
                        store.clear();saved_signature=None
                        db.put('naver_auth',{'state':'unverified','checkedAt':None,'message':'로그인 세션이 종료됐습니다. 다시 로그인하세요.'})
                    context.pages[0].wait_for_timeout(1000)
                except Problem:raise
                except Exception:break
        finally:
            try:context.close()
            except Exception:pass
