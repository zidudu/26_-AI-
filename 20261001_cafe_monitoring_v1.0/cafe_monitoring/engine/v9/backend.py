"""One persistent browser, isolated cafe collections, one validated combined PPT."""
from contextlib import ExitStack, contextmanager, asynccontextmanager
from copy import deepcopy
from pathlib import Path
import traceback
import hashlib
import time
from html import escape
from v8.backend import Backend as SingleBackend
from v8.configuration import V8Error, read_json, write_json
from v9.identity import resolve
from v9.configuration import PERFORMANCE_DEFAULTS
from v9.ai_provider import load_settings, check_codex, validate_settings
from v9 import __version__


class Backend:
    def __init__(self,root):
        self.root=Path(root).resolve()
        for rel,expected in read_json(Path(__file__).with_name('engine_manifest.json')).items():
            path=self.root/rel
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise V8Error('ENGINE_VERSION_MISMATCH','검증한 V8.2 엔진 파일과 다릅니다: '+rel)
        self.base=SingleBackend(self.root)
        self.cfg=deepcopy(self.base.cfg)
        self.cfg['out_root']=self.root/'output_v9'
        self.legacy_out=self.base.legacy_out
        self.words=self.base.words
        self.webcfg=self.base.webcfg
        self.context=None
        self.performance=deepcopy(PERFORMANCE_DEFAULTS)
        self.ai_settings=load_settings(self.root)

    def check(self):
        ai=validate_settings(self.ai_settings)
        if ai['provider']=='codex':
            from v754.powerpoint import check_office
            from v754.analysis_config import load_analysis_config
            import playwright.sync_api
            check_office()
            load_analysis_config(self.root)
            status=check_codex(ai)
            return {'provider':'codex','keywords':self.words,'model':ai['codex']['model'],
                    'browser':self.webcfg['browser'],'codex':status}
        settings={**self.base.check(), 'provider':'openai'}
        return {k:v for k,v in settings.items() if k!='cafe_id'}

    @asynccontextmanager
    async def async_browser(self):
        from playwright.async_api import async_playwright, TimeoutError
        from v754.collect.run_lock import RunLock
        from v9.ppt_measurements import measurement_session
        with RunLock(self.webcfg['profile_lock']), measurement_session():
            async with async_playwright() as pw:
                opts=dict(user_data_dir=str(self.webcfg['profile_dir']),headless=False,
                          locale='ko-KR',timezone_id='Asia/Seoul',viewport={'width':1365,'height':900})
                if self.webcfg['browser']!='chromium': opts['channel']=self.webcfg['browser']
                self.context=await pw.chromium.launch_persistent_context(**opts)
                self.timeout_error=TimeoutError
                try:
                    yield self
                finally:
                    try: await self.context.close()
                    finally: self.context=None

    async def identify_async(self,cafe):
        from v9.identity import resolve_async
        from v9.collection_control import pace, check_stop, report_access_error
        await pace()
        page=await self.context.new_page()
        try:
            check_stop()
            return await resolve_async(page,cafe)
        except Exception as exc:
            report_access_error(exc)
            raise
        finally:
            await page.close()

    async def collect_async(self,cafe,folder,start,end):
        from v754.main_v754 import report_base
        from v754.period import Window
        from v9.async_collect.period_collect import collect_with_pages
        from v9.collection_control import log, keyword, check_stop, report_access_error
        if not cafe.get('club_id'): raise V8Error('CAFE_ID_UNRESOLVED','clubId가 필요합니다.')
        report=report_base('period_collection')
        window=Window(start,end)
        report.update(window=window.record(),keywords=self.words,cafe_id=cafe['club_id'],
                      cafe_slug=cafe['slug'],cafe_name=cafe['name'],search_scope='title',
                      search_board='all',search_sort='latest',collector_mode='9.3_async')
        cfg=deepcopy(self.cfg)
        cfg['cafe_names'][cafe['club_id']]=cafe['name']
        webcfg={**self.webcfg,'cafe_id':cafe['club_id'],'cafe_slug':cafe['slug'],
                'ppt_image_ppi':self.performance.get('ppt_image_ppi', 125),
                'ppt_capture_columns':cfg['capture_columns']}
        checkpoint=lambda: write_json(folder/'summary.json',report)
        pages=[]
        started=time.perf_counter()
        checkpoint()
        try:
            check_stop()
            pages.append(await self.context.new_page())
            pages.append(await self.context.new_page())
            settings={**self.base.settings, 'search_recheck': self.performance['search_recheck']}
            await collect_with_pages(cfg,settings,webcfg,folder,report,checkpoint,window,self.words,
                                     search_page=pages[0],article_page=pages[1],timeout_error=self.timeout_error)
            if not report.get('collection_complete'):
                audit=read_json(folder/'search_audit.json')
                access=next((r.get('reason') for r in audit.get('keyword_results',[])
                             if r.get('reason') in {'REQUEST_BLOCKED','LOGIN_REQUIRED','AUTH_REQUIRED'}),None)
                if access: raise V8Error(access,'접근·로그인 확인이 필요하여 웹 요청을 중단합니다.')
                raise V8Error('COLLECTION_INCOMPLETE','검색·작성 시각 확인이 끝나지 않았습니다.')
            report['status']='collected' if report['items'] else 'collected_empty'
        except BaseException as exc:
            report_access_error(exc)
            report.update(status='failed',error={'code':getattr(exc,'code',type(exc).__name__), 'message':str(exc)})
            (folder/'error_traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
            raise
        finally:
            keyword('')
            elapsed=round(time.perf_counter()-started,3)
            report.setdefault('timings',{})['collection_seconds']=elapsed
            checkpoint()
            log(f'[소요 시간] collection / {elapsed:.1f}초',flush=True)
            for page in reversed(pages):
                try: await page.close()
                except Exception:
                    log('[탭 닫기 확인 필요] 브라우저 종료 시 함께 정리합니다.',flush=True)

    @contextmanager
    def browser(self):
        from playwright.sync_api import sync_playwright, TimeoutError
        from v754.collect.run_lock import RunLock
        from v9.ppt_measurements import measurement_session
        with ExitStack() as stack:
            stack.enter_context(RunLock(self.webcfg['profile_lock']))
            stack.enter_context(measurement_session())
            pw=stack.enter_context(sync_playwright())
            opts=dict(user_data_dir=str(self.webcfg['profile_dir']),headless=False,
                      locale='ko-KR',timezone_id='Asia/Seoul',viewport={'width':1365,'height':900})
            if self.webcfg['browser']!='chromium': opts['channel']=self.webcfg['browser']
            self.context=pw.chromium.launch_persistent_context(**opts)
            stack.callback(self.context.close)
            self.timeout_error=TimeoutError
            try: yield self
            finally: self.context=None

    def identify(self,cafe):
        if self.context is None: raise V8Error('BROWSER_NOT_READY','브라우저 연결이 없습니다.')
        page=self.context.new_page()
        try: return resolve(page,cafe)
        finally: page.close()

    def collect(self,cafe,folder,start,end):
        from v754.main_v754 import report_base, timed
        from v754.period import Window
        from v9.period_collect import collect_with_pages
        if not cafe.get('club_id'): raise V8Error('CAFE_ID_UNRESOLVED','clubId가 필요합니다.')
        report=report_base('period_collection')
        window=Window(start,end)
        report.update(window=window.record(),keywords=self.words,cafe_id=cafe['club_id'],
                      cafe_slug=cafe['slug'],cafe_name=cafe['name'],search_scope='title',
                      search_board='all',search_sort='latest')
        cfg=deepcopy(self.cfg)
        cfg['cafe_names'][cafe['club_id']]=cafe['name']
        webcfg={**self.webcfg,'cafe_id':cafe['club_id'],'cafe_slug':cafe['slug'],
                'ppt_image_ppi':self.performance.get('ppt_image_ppi', 125),
                'ppt_capture_columns':cfg['capture_columns']}
        checkpoint=lambda: write_json(folder/'summary.json',report)
        checkpoint()
        search=self.context.new_page()
        article=self.context.new_page()
        try:
            with timed(report,'collection',checkpoint):
                settings={**self.base.settings, 'search_recheck': self.performance['search_recheck']}
                collect_with_pages(cfg,settings,webcfg,folder,report,checkpoint,window,self.words,
                                   search_page=search,article_page=article,timeout_error=self.timeout_error)
            if not report.get('collection_complete'):
                audit_path=folder/'search_audit.json'
                audit=read_json(audit_path) if audit_path.exists() else {}
                if any(row.get('reason')=='REQUEST_BLOCKED' for row in audit.get('keyword_results',[])):
                    raise V8Error('REQUEST_BLOCKED','접근 제한이 감지되어 나머지 카페 요청도 중단합니다.')
                raise V8Error('COLLECTION_INCOMPLETE','검색·작성 시각 확인이 끝나지 않았습니다.')
            report['status']='collected' if report['items'] else 'collected_empty'
            checkpoint()
        except BaseException as exc:
            report.update(status='failed',error={'code':getattr(exc,'code',type(exc).__name__), 'message':str(exc)})
            (folder/'error_traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
            checkpoint()
            raise
        finally:
            article.close()
            search.close()

    def load_collection(self,cafe,folder,start,end):
        from v754.period_sources import load_collection
        data,items=load_collection(folder)
        if (data['cafe_id']!=cafe['club_id'] or set(data['keywords'])!=set(self.words)
                or data['window']['start']!=start.isoformat() or data['window']['end']!=end.isoformat()):
            raise V8Error('COLLECTION_MISMATCH','카페·키워드·기간이 다른 수집 결과입니다.')
        for item in items: item['cafe_slug']=cafe['slug']
        return items

    def export(self,items,folder,cafes):
        from v754.main_v754 import report_base
        from v9.exporting import export_items
        report=report_base('v9_multi_cafe_export')
        report.update(version='9.0.0',collection_complete=True,range_search_complete=True,
                      articles_verified_complete=True,cafes=deepcopy(cafes),selected_articles=len(items),
                      article_keys=[f"{a['cafe_id']}:{a['id']}" for a in items])
        checkpoint=lambda: write_json(folder/'summary.json',report)
        cfg=deepcopy(self.cfg)
        cfg['cafe_names'].update({c['club_id']:c['name'] for c in cafes if c.get('club_id')})
        cfg['v93_performance']=deepcopy(self.performance)
        cfg['v95_ai_settings']=deepcopy(self.ai_settings)
        report['performance']=deepcopy(self.performance)
        report['application_version']=__version__
        report['ai_settings']=deepcopy(self.ai_settings)
        from v9.dashboard_data import context
        context(cfg,report,items,cafes,self.words)
        report['dashboard_settings']=deepcopy(cfg['v96_dashboard'])
        checkpoint()
        code=export_items(items,cfg,folder,report,checkpoint)
        report['v9_article_keys']=[f"{a['cafe_id']}:{a['id']}" for a in report.get('items',[])]
        # Existing renderer validates each slide against its source article.
        # Add composite identities to the diagnostics without changing originals.
        for plan,item in zip(report.get('article_plans',[]),report.get('items',[])):
            plan.update(cafe_id=item['cafe_id'],cafe_name=item['cafe_name'],article_key=f"{item['cafe_id']}:{item['id']}")
        comparison=folder/'comparison.html'
        if comparison.exists():
            text=comparison.read_text(encoding='utf-8')
            audit=read_json(folder/'analysis_audit.json')
            names={c['club_id']:c['name'] for c in cafes if c.get('club_id')}
            # Replace heading occurrences in audit order so equal IDs from two
            # cafes remain distinguishable. Escape all external text.
            for row in audit['items']:
                aid=str(row['article_id']);cid=str(row['cafe_id'])
                original='<article><h2>'+escape(aid,quote=True)+' · '
                replacement='<article><h2>'+escape(names.get(cid,cid)+' · '+cid+':'+aid,quote=True)+' · '
                text=text.replace(original,replacement,1)
            text=text.replace('V7.5.4.1 원문·분석 비교','V9 원문·분석 비교')
            comparison.write_text(text,encoding='utf-8')
        checkpoint()
        return report,code
