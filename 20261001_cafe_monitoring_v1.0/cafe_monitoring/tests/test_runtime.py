import contextlib
import http.client
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v10.common import Problem,stamp,KST,digest,write_json
from v10.database import Database
from v10.service import Service
from v10.settings import defaults,validate,next_schedule,keyword_id
from v10.worker import execute


def config():
    c=defaults();c.update(sendMail=False,scheduleEnabled=False,stepAnalyze=False,stepPpt=False)
    c['selectedCafeIds']=c['selectedCafeIds'][:2]
    return validate(c)


def article(cid='101',aid='42',body='원문  본문\n두 줄',keywords=None):
    source=dict(cafe_id=cid,article_id=aid,title='후방 카메라 문의',body=body,body_raw=body,
                written_at='2026-09-29T10:00:00+09:00',collected_at='2026-09-30T10:00:00+09:00',
                url=f'https://cafe.naver.com/ca-fe/cafes/{cid}/articles/{aid}',media=[])
    item=dict(cafe_id=cid,id=aid,title=source['title'],url=source['url'],written_at=source['written_at'],
              collected_at=source['collected_at'],matched_keywords=keywords or ['후방'],source_artifact_sha256=digest(source))
    return item,source


class FakeEngine:
    """Explicit offline double. Never reachable from normal application code."""
    def __init__(self,db,rid,cfg,folder):self.db,self.rid,self.ui,self.folder=db,rid,cfg,folder
    def __enter__(self):return self
    def __exit__(self,*_):pass
    async def collect(self,cafes,bounds,callback):
        for n,c in enumerate(cafes):
            self.db.check_stop(self.rid);item,source=article(str(101+n))
            self.db.resolve_cafe(c['id'],item['cafe_id']);callback(c,[item],[source],None)
    def report(self,items,cafes):return dict(items=deepcopy(items),dashboard_stats={'cafes':cafes},analysis_review_count=0)
    @contextlib.contextmanager
    def analysis_context(self,items,report,checkpoint):
        analyzed=[]
        for a in items:
            a=deepcopy(a);a.update(analysis_candidate={'document_type':'question_information'},document_type='question_information',display_text='검증용 분석',analysis_bindings=[{'text':'원문  본문'}]);analyzed.append(a)
        class Analysis:
            articles=analyzed
            def snapshot(self):return deepcopy(self.articles)
        report['items']=analyzed;checkpoint();yield Analysis()
    def render(self,items,report,checkpoint,analysis=None):
        for i in items:i['display_text']='PPT 표시 분석'
        p=self.folder/'offline_test_only.pptx';p.write_bytes(b'OFFLINE TEST DOUBLE - NOT A REAL PPT')
        report.update(items=items,pptx=str(p),slides=3,dashboard_slides=3);checkpoint();return p


class FakeOffice:
    calls=0
    @staticmethod
    def presentation_outputs(path,folder,prefix_count,summary=False):return [],None
    @classmethod
    def send_mail(cls,cfg,attachment,rid):cls.calls+=1;return {'state':'submitted','sender':'test@example.invalid'}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name,launch=False);self.db=self.s.db;self.cfg=config()
    def tearDown(self):self.tmp.cleanup()
    def run_new(self,cfg=None,request='r'):
        r=self.s.create_run(cfg or self.cfg,request);return r['id']
    def finish(self,rid):self.db.update_run(rid,state='completed',finish=True)

    def test_db_created_empty_no_sample_posts(self):
        self.assertEqual(self.db.counts()['posts'],0);self.assertEqual(self.db.counts()['runs'],0)
        self.assertEqual(len(self.db.catalog()['cafes']),9)

    def test_immutable_settings_snapshot_and_optimistic_edit(self):
        rid=self.run_new();first=self.db.run(rid)['settings']['catalog']['cafes'][0]['name']
        cfg=deepcopy(self.cfg);cfg['catalog']['cafes'][0]['name']='이름 변경'
        self.assertEqual(self.db.save_settings(cfg,0),1)
        self.assertEqual(self.db.run(rid)['settings']['catalog']['cafes'][0]['name'],first)
        with self.assertRaises(Problem):self.db.save_settings(self.cfg,0)

    def test_one_worker_and_idempotent_run_request(self):
        rid=self.run_new();self.assertEqual(self.run_new(request='r'),rid)
        with self.assertRaises(Problem):self.run_new(request='second')

    def test_unsaved_execution_catalog_does_not_replace_saved_catalog(self):
        original=self.db.catalog()['cafes'][0]['name']
        cfg=deepcopy(self.cfg);cfg['catalog']['cafes'][0]['name']='이번 실행의 임시 이름'
        rid=self.run_new(cfg)
        self.assertEqual(self.db.catalog()['cafes'][0]['name'],original)
        self.assertEqual(self.db.run(rid)['settings']['catalog']['cafes'][0]['name'],'이번 실행의 임시 이름')

    def test_concurrent_clicks_create_only_one_run(self):
        barrier=threading.Barrier(2);results=[]
        def create(n):
            barrier.wait()
            try:results.append(self.run_new(request=str(n)))
            except Problem:results.append(None)
        threads=[threading.Thread(target=create,args=(n,)) for n in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(bool(r) for r in results),1)

    def test_composite_article_identity_and_keyword_counts(self):
        rid=self.run_new();uids=self.cfg['selectedCafeIds']
        for uid,cid in zip(uids,['101','102']):
            i,s=article(cid,keywords=['후방','카메라']);self.db.store_article(rid,uid,i,s)
        self.assertEqual(self.db.counts()['posts'],2)
        self.assertEqual(sum(len(p['keys']) for p in self.db.stats()),4)

    def test_dedupe_adds_matches_and_changed_body_creates_version(self):
        uid=self.cfg['selectedCafeIds'][0];i,s=article();r1=self.run_new()
        self.db.store_article(r1,uid,i,s);self.finish(r1)
        r2=self.run_new(request='r2');j=deepcopy(i);j['matched_keywords']=['후방','제동']
        x=self.db.store_article(r2,uid,j,s,dedupe=True);self.assertFalse(x['included'])
        self.assertEqual(self.db.stats()[0]['keys'],['후방','제동'])
        self.finish(r2);r3=self.run_new(request='r3');j,s2=article(body='변경된 본문')
        x=self.db.store_article(r3,uid,j,s2,dedupe=True);self.assertTrue(x['included']);self.assertEqual(x['disposition'],'changed')
        self.assertEqual(self.db.counts()['post_versions'],2)
        self.assertEqual(self.db.articles(r1)[0]['raw'],s['body_raw'])

    def test_review_is_analysis_specific_persistent_and_visible_in_stats(self):
        uid=self.cfg['selectedCafeIds'][0];rid=self.run_new();i,s=article()
        self.db.store_article(rid,uid,i,s);i.update(display_text='분석',analysis_candidate={'ok':True})
        self.db.save_analysis(rid,uid,i);a=self.db.articles(rid)[0]
        self.db.review(a['analysisId']);self.assertEqual(Database(self.tmp.name).stats()[0]['status'],'검토완료')
        self.finish(rid);r2=self.run_new(request='r2');i,s=article(body='내용 변경')
        self.db.store_article(r2,uid,i,s)
        self.assertEqual(self.db.stats()[0]['status'],'미검토');self.assertIsNone(self.db.stats()[0]['analysisId'])

    def test_old_import_cannot_replace_newer_version(self):
        uid=self.cfg['selectedCafeIds'][0];r1=self.run_new();i,s=article(body='최신')
        self.db.store_article(r1,uid,i,s,observed='2026-09-30T10:00:00+09:00');self.finish(r1)
        r2=self.run_new(request='r2');i,s=article(body='예전')
        self.db.store_article(r2,uid,i,s,observed='2026-09-20T10:00:00+09:00')
        self.assertEqual(self.db.stats()[0]['raw'],'최신')
        self.assertEqual(self.db.stats()[0]['firstCollectedAt'],'2026-09-20T10:00:00+09:00')

    def test_cursors_per_cafe_and_custom_range_does_not_advance(self):
        rid=self.run_new();u1,u2=self.cfg['selectedCafeIds'];self.db.cafe_outcome(rid,u1,'completed',1,advance=True)
        self.db.cafe_outcome(rid,u2,'failed',error='네트워크 오류');self.finish(rid)
        self.assertIn(u1,self.db.cursors());self.assertNotIn(u2,self.db.cursors())
        cfg=deepcopy(self.cfg);cfg.update(range='기간 직접 지정',periodStart='2026-09-01T00:00',periodEnd='2026-09-02T00:00')
        rid=self.run_new(cfg,'period');before=self.db.cursors();execute(self.db,rid,engine_factory=FakeEngine)
        self.assertEqual(self.db.cursors(),before)

    def test_since_previous_requires_initial_per_cafe_cursor(self):
        cfg=deepcopy(self.cfg);cfg['range']='이전 실행 이후'
        with self.assertRaises(Problem):self.s.period(cfg)

    def test_automatic_period_freezes_minutes_in_db_and_completed_cursors(self):
        at=datetime(2026,9,30,13,29,54,123456,tzinfo=KST)
        for hours in (24,48):
            with self.subTest(hours=hours):
                cfg=deepcopy(self.cfg);cfg['range']=f'최근 {hours}시간'
                r=self.s.create_run(cfg,str(hours),at=at)
                expected_end='2026-09-30T13:29:00+09:00'
                self.assertEqual(r['periodStart'],stamp(at.replace(second=0,microsecond=0)-timedelta(hours=hours)))
                self.assertEqual(r['periodEnd'],expected_end)
                for row in r['cafeResults']:
                    self.assertEqual((row['start_at'],row['end_at']),(r['periodStart'],expected_end))
                    self.db.cafe_outcome(r['id'],row['cafe_id'],'completed',0,advance=True)
                self.assertEqual(set(self.db.cursors().values()),{expected_end})
                self.finish(r['id'])

    def test_legacy_second_cursors_round_back_per_cafe_without_gap(self):
        at=datetime(2026,9,30,13,29,54,tzinfo=KST)
        previous=['2026-09-29T10:00:37+09:00','2026-09-29T11:05:59+09:00']
        bounds={uid:('2026-09-28T00:00:00+09:00',end) for uid,end in zip(self.cfg['selectedCafeIds'],previous)}
        self.db.create_run('legacy',self.cfg,bounds,request_id='legacy')
        for uid in bounds:self.db.cafe_outcome('legacy',uid,'completed',0,advance=True)
        self.finish('legacy');saved=self.db.cursors()
        cfg=deepcopy(self.cfg);cfg['range']='이전 실행 이후'
        for days in (0,1,2):
            with self.subTest(overlap=days):
                cfg['overlap']=str(days);actual=self.s.period(cfg,at)
                for uid,end in zip(cfg['selectedCafeIds'],previous):
                    expected=datetime.fromisoformat(end).replace(second=0)-timedelta(days=days)
                    self.assertEqual(actual[uid],(stamp(expected),'2026-09-30T13:29:00+09:00'))
        self.assertEqual(self.db.cursors(),saved)

    def test_same_minute_resume_rejected_before_run_creation(self):
        at=datetime(2026,9,30,13,29,54,tzinfo=KST)
        r=self.s.create_run(self.cfg,'first',at=at)
        for uid in self.cfg['selectedCafeIds']:self.db.cafe_outcome(r['id'],uid,'completed',0,advance=True)
        self.finish(r['id']);cfg=deepcopy(self.cfg);cfg.update(range='이전 실행 이후',overlap='0')
        with self.assertRaisesRegex(Problem,'시간 구간'):
            self.s.create_run(cfg,'empty',at=at)
        self.assertEqual(self.db.counts()['runs'],1)
        next_run=self.s.create_run(cfg,'next-minute',at=at+timedelta(minutes=1))
        self.assertEqual(next_run['periodStart'],r['periodEnd'])
        self.assertEqual(next_run['periodEnd'],'2026-09-30T13:30:00+09:00')

    def test_custom_period_seconds_rejected_before_run_creation(self):
        cfg=deepcopy(self.cfg);cfg.update(range='기간 직접 지정',periodStart='2026-09-01T10:00:00+09:00',periodEnd='2026-09-02T10:00:00+09:00')
        for key in ('periodStart','periodEnd'):
            for seconds in ('01','00.000001'):
                with self.subTest(key=key,seconds=seconds):
                    bad=deepcopy(cfg);bad[key]=bad[key].replace(':00+09:00',':'+seconds+'+09:00')
                    with self.assertRaisesRegex(Problem,'분 단위'):self.s.create_run(bad,key+seconds)
        self.assertEqual(self.db.counts()['runs'],0)

    def test_custom_minute_period_preserved_and_does_not_advance_cursor(self):
        cfg=deepcopy(self.cfg);cfg.update(range='기간 직접 지정',periodStart='2026-09-01T10:03',periodEnd='2026-09-02T01:07:00Z')
        r=self.s.create_run(cfg,'custom-minute')
        self.assertEqual(r['periodStart'],'2026-09-01T10:03:00+09:00')
        self.assertEqual(r['periodEnd'],'2026-09-02T10:07:00+09:00')
        execute(self.db,r['id'],engine_factory=FakeEngine)
        self.assertEqual(self.db.cursors(),{})

    def test_full_pipeline_preserves_pre_render_snapshot(self):
        cfg=deepcopy(self.cfg);cfg.update(stepAnalyze=True,stepPpt=True)
        rid=self.run_new(cfg);execute(self.db,rid,engine_factory=FakeEngine,office=FakeOffice)
        self.assertEqual(self.db.run(rid)['state'],'completed')
        self.assertEqual(self.db.counts()['analyses'],4)
        with self.db.connect() as c:
            initial=json.loads(c.execute("SELECT result_json FROM analyses WHERE phase='analysis' LIMIT 1").fetchone()[0])
            rendered=json.loads(c.execute("SELECT result_json FROM analyses WHERE phase='render' LIMIT 1").fetchone()[0])
        self.assertEqual(initial['display_text'],'검증용 분석');self.assertEqual(rendered['display_text'],'PPT 표시 분석')

    def test_ppt_only_reuses_exact_run_and_never_calls_analysis_or_mail(self):
        cfg=deepcopy(self.cfg);cfg.update(stepAnalyze=True,stepPpt=True)
        rid=self.run_new(cfg);execute(self.db,rid,engine_factory=FakeEngine,office=FakeOffice)
        class RebuildOnly(FakeEngine):
            def analysis_context(self,*_):raise AssertionError('AI called during rebuild')
            async def collect(self,*_):raise AssertionError('collection called during rebuild')
        cfg.update(stepCollect=False,stepAnalyze=False,sourceRun=rid)
        r2=self.run_new(cfg,'rebuild');FakeOffice.calls=0
        execute(self.db,r2,engine_factory=RebuildOnly,office=FakeOffice)
        self.assertEqual(self.db.run(r2)['state'],'completed');self.assertEqual(FakeOffice.calls,0)
        self.assertEqual(self.db.counts()['posts'],2)

    def test_partial_cafe_failure_retains_successful_cursor(self):
        class Partial(FakeEngine):
            async def collect(self,cafes,bounds,callback):
                i,s=article();self.db.resolve_cafe(cafes[0]['id'],'101');callback(cafes[0],[i],[s],None)
                callback(cafes[1],[],[],'PAGE_NOT_READY')
        rid=self.run_new();execute(self.db,rid,engine_factory=Partial)
        self.assertEqual(self.db.run(rid)['state'],'partial');self.assertEqual(len(self.db.cursors()),1)

    def test_stop_preserves_completed_collection(self):
        class Stop(FakeEngine):
            async def collect(self,cafes,bounds,callback):
                i,s=article();callback(cafes[0],[i],[s],None);self.db.stop(self.rid);self.db.check_stop(self.rid)
        rid=self.run_new();execute(self.db,rid,engine_factory=Stop)
        self.assertEqual(self.db.run(rid)['state'],'cancelled');self.assertEqual(self.db.counts()['posts'],1)
        with self.assertRaises(InterruptedError):self.db.mail_start(rid,{},None)

    def test_mail_uncertainty_is_not_automatically_replayed(self):
        class Uncertain(FakeOffice):
            calls=0
            @classmethod
            def send_mail(cls,*_):cls.calls+=1;raise TimeoutError('Send response lost')
        cfg=deepcopy(self.cfg);cfg.update(stepAnalyze=True,stepPpt=True,sendMail=True,mailTo='test@example.invalid')
        rid=self.run_new(cfg);execute(self.db,rid,engine_factory=FakeEngine,office=Uncertain)
        self.assertEqual(self.db.run(rid)['state'],'mail_unknown');self.assertEqual(Uncertain.calls,1)
        self.db.recover();self.assertEqual(self.db.run(rid)['state'],'mail_unknown')
        with self.assertRaises(Problem):self.db.mail_start(rid,{},None)
        self.assertEqual(len(self.db.cursors()),2)

    def test_server_restart_recovers_interrupted_run(self):
        rid=self.run_new();self.db.recover();self.assertEqual(self.db.run(rid)['state'],'interrupted')
        self.assertIsNone(self.db.active())

    def test_artifact_modified_or_removed_cannot_be_opened(self):
        # Test binary integrity with binary data, not an invalid Office document.
        rid=self.run_new();p=Path(self.tmp.name)/'integrity_fixture.bin';p.write_bytes(b'one')
        aid=self.db.add_artifact(rid,'test',p);self.assertEqual(self.db.artifact(aid)['size'],p.stat().st_size)
        original=self.db.artifact(aid)['sha256']
        p.write_bytes(b'two')
        import hashlib
        self.assertNotEqual(original,hashlib.sha256(p.read_bytes()).hexdigest())
        with self.assertRaises(Problem) as cm:self.db.artifact(aid)
        self.assertEqual(cm.exception.code,'FILE_CHANGED')
        p.unlink()
        with self.assertRaises(Problem) as cm:self.db.artifact(aid)
        self.assertEqual(cm.exception.code,'FILE_MISSING')

    def test_calendar_scheduler_weekend_and_one_daily_execution(self):
        cfg=deepcopy(self.cfg);cfg.update(scheduleEnabled=True,scheduleTime='01:25',weekdaysOnly=True)
        friday=datetime(2026,10,2,2,0,tzinfo=KST)
        self.assertEqual(next_schedule(cfg,friday),'2026-10-05T01:25:00+09:00')
        self.db.save_settings(cfg,0);self.s.started_at=datetime(2026,9,30,1,0,tzinfo=KST)
        due=datetime(2026,9,30,1,25,tzinfo=KST);self.s.schedule_tick(due);self.s.schedule_tick(due)
        self.assertEqual(self.db.counts()['runs'],1)

    def test_closed_server_does_not_replay_missed_schedule(self):
        cfg=deepcopy(self.cfg);cfg.update(scheduleEnabled=True,scheduleTime='01:25')
        self.db.save_settings(cfg,0);self.s.started_at=datetime(2026,9,30,10,tzinfo=KST)
        self.s.schedule_tick(datetime(2026,9,30,10,1,tzinfo=KST));self.assertEqual(self.db.counts()['runs'],0)

    def test_settings_fixed_policies_validation_and_normalization(self):
        cfg=deepcopy(self.cfg);cfg.update(evidence=False,humanReview=False)
        checked=validate(cfg);self.assertTrue(checked['evidence']);self.assertTrue(checked['humanReview'])
        self.assertNotEqual(keyword_id('전방 카메라'),keyword_id('전방카메라'))
        cfg['catalog']['cafes'][0]['url']='https://evil.example/bestcm'
        with self.assertRaises(Problem):validate(cfg)

    def test_dynamic_dashboard_and_summary_toggle(self):
        sys.path.insert(0,str(ROOT/'engine'))
        from v9.dashboard_data import build_stats,DEFAULTS
        from v9.powerpoint import dashboard_prefix_count
        opts=deepcopy(DEFAULTS)
        cafes=[dict(code='NEW',name='새 카페',slug='newcafe',club_id='999',status='collected',count=1)]
        items=[dict(cafe_id='999',id='1',matched_keywords=['후방'])]
        stats=build_stats(items,cafes,['후방'],opts,catalog=[('NEW','새 카페','newcafe','999')])
        self.assertEqual(stats['total_posts'],1);self.assertEqual(stats['cafes'][0]['code'],'NEW')
        self.assertEqual(dashboard_prefix_count({'v96_dashboard':{'include_cafe_summaries':True}},{'dashboard_stats':stats}),2)
        self.assertEqual(dashboard_prefix_count({'v96_dashboard':{'include_cafe_summaries':False}},{'dashboard_stats':stats}),1)

    def test_database_backup_is_readable(self):
        self.run_new();path=self.db.backup(Path(self.tmp.name)/'backup.sqlite3')
        import sqlite3
        with contextlib.closing(sqlite3.connect(path)) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],1)

    def check_backup_connections(self,fail=False):
        import sqlite3
        real_connect=sqlite3.connect;connections=[]
        class Tracked(sqlite3.Connection):
            closed=False
            def close(self):
                self.closed=True
                super().close()
            def backup(self,target,*args,**kwargs):
                if fail:raise sqlite3.OperationalError('injected backup failure')
                return super().backup(target,*args,**kwargs)
        def connect(*args,**kwargs):
            c=real_connect(*args,factory=Tracked,**kwargs);connections.append(c);return c
        try:
            with patch('v10.database.sqlite3.connect',connect):
                if fail:
                    with self.assertRaisesRegex(sqlite3.OperationalError,'injected backup failure'):
                        self.db.backup(Path(self.tmp.name)/'backup.sqlite3')
                else:self.db.backup(Path(self.tmp.name)/'backup.sqlite3')
            self.assertEqual(len(connections),2)
            self.assertTrue(all(c.closed for c in connections),'Backup source and destination must both close')
            for c in connections:
                with self.assertRaises(sqlite3.ProgrammingError):c.execute('SELECT 1')
        finally:
            for c in connections:
                if not c.closed:c.close()

    def test_backup_closes_connections_on_success(self):
        self.check_backup_connections()

    def test_backup_closes_connections_on_failure(self):
        self.check_backup_connections(fail=True)


class HttpTests(unittest.TestCase):
    def setUp(self):
        from v10.server import AppServer
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name,launch=False)
        self.server=AppServer(('127.0.0.1',0),self.s);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def request(self,path,data=None,headers=None):
        h={'Content-Type':'application/json','X-V10-Token':self.server.token};h.update(headers or {})
        req=urllib.request.Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        # All test URLs are our own 127.0.0.1 server; no global proxy settings.
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req,timeout=5)

    def test_real_http_run_state_review_and_stats(self):
        with self.request('/api/runs',{'settings':config(),'requestId':'http-run'}) as r:rid=json.load(r)['id']
        cfg=self.s.db.run(rid)['settings'];execute(self.s.db,rid,engine_factory=FakeEngine)
        with self.request('/api/state') as r:state=json.load(r)
        self.assertEqual(state['counts']['posts'],2);self.assertEqual(state['runs'][0]['state'],'completed')
        with self.request('/api/posts?limit=1') as r:p=json.load(r)
        self.assertEqual(len(p['items']),1);self.assertIsNotNone(p['next'])
        with self.request('/') as r:self.assertIn('window.V10_TOKEN=',r.read().decode())

    def test_post_requires_token_and_same_origin(self):
        for headers in ({'X-V10-Token':''},{'Origin':'https://evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/backup',{},headers)
            with cm.exception as response:
                self.assertEqual(response.code,403)
                self.assertIn(json.load(response)['error'],('INVALID_TOKEN','INVALID_ORIGIN'))

    def test_codex_routes_require_token_and_origin_and_poll_without_db_change(self):
        # A GET never launches authentication; request payload cannot pick a command.
        with patch.object(self.s,'codex_request',return_value={'state':'starting','busy':True}) as start:
            for path in ('/api/codex/login','/api/codex/check'):
                for headers in ({'X-V10-Token':''},{'Origin':'https://evil.example'}):
                    with self.assertRaises(urllib.error.HTTPError) as cm:self.request(path,{},headers)
                    with cm.exception as response:self.assertEqual(response.code,403)
                start.assert_not_called()
                with self.request(path,{'command':'arbitrary-command'}) as response:
                    self.assertEqual(response.status,202)
                start.assert_called_once_with(path.rsplit('/',1)[1]);start.reset_mock()
            seq=self.s.db.get('sequence',0)
            self.s.codex._set('connected','ChatGPT 로그인 확인 완료')
            with self.request('/api/state?since='+str(seq)) as response:state=json.load(response)
            self.assertTrue(state['unchanged'])
            self.assertEqual(state['codexAuth']['state'],'connected')
            start.assert_not_called()

    def test_naver_login_actions_are_protected_and_confirm_is_explicit(self):
        with patch.object(self.s,'login',return_value={'needsConfirmation':True}) as login, \
             patch.object(self.s,'confirm_login',return_value={'message':'confirmed'}) as confirm, \
             patch.object(self.s,'cancel_login',return_value={'message':'cancelled'}) as cancel:
            for path,action in [('/api/login',login),('/api/login/confirm',confirm),('/api/login/cancel',cancel)]:
                for headers in ({'X-V10-Token':''},{'Origin':'https://evil.example'}):
                    with self.assertRaises(urllib.error.HTTPError) as cm:self.request(path,{},headers)
                    with cm.exception as response:self.assertEqual(response.code,403)
                action.assert_not_called()
            for body,wanted in [({},False),({'confirm':'true'},False),({'confirm':True},True)]:
                with self.request('/api/login',body) as response:json.load(response)
                login.assert_called_with(confirm=wanted)
            with self.request('/api/login/confirm',{}) as response:self.assertEqual(json.load(response)['message'],'confirmed')
            with self.request('/api/login/cancel',{}) as response:self.assertEqual(json.load(response)['message'],'cancelled')

    def test_rejected_post_waits_for_split_body(self):
        from v10.server import Handler
        headers_ok,finish=Handler.headers_ok,Handler.finish
        for headers,code in (({'X-V10-Token':''},'INVALID_TOKEN'),
                             ({'Origin':'https://evil.example'},'INVALID_ORIGIN')):
            with self.subTest(code=code):
                checked=threading.Event();finished=threading.Event()
                def observed_headers(handler,*args,**kwargs):
                    try:return headers_ok(handler,*args,**kwargs)
                    finally:checked.set()
                def observed_finish(handler):
                    try:return finish(handler)
                    finally:finished.set()
                conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
                try:
                    with patch.object(Handler,'headers_ok',observed_headers),patch.object(Handler,'finish',observed_finish):
                        conn.putrequest('POST','/api/backup')
                        for k,v in {'Content-Type':'application/json','Content-Length':'2',
                                    'X-V10-Token':self.server.token,**headers}.items():conn.putheader(k,v)
                        conn.endheaders();conn.send(b'{')
                        self.assertTrue(checked.wait(2),'Server did not inspect headers')
                        self.assertFalse(finished.wait(0.1),'Server closed while the request body was still arriving')
                        conn.send(b'}')
                        with conn.getresponse() as r:
                            self.assertEqual(r.status,403);self.assertEqual(json.load(r)['error'],code)
                        self.assertTrue(finished.wait(2))
                finally:conn.close()
        self.assertFalse((self.s.db.directory/'backups').exists())

    def test_rejected_incomplete_body_has_bounded_wait(self):
        from v10.server import Handler
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        try:
            with patch.object(Handler,'REJECT_DRAIN_TIMEOUT',0.05,create=True):
                conn.putrequest('POST','/api/backup')
                conn.putheader('Content-Type','application/json');conn.putheader('Content-Length','2')
                conn.endheaders();conn.send(b'{')
                with conn.getresponse() as r:
                    self.assertEqual(r.status,403);self.assertEqual(json.load(r)['error'],'INVALID_TOKEN')
        finally:conn.close()
        self.assertFalse((self.s.db.directory/'backups').exists())

    def test_loopback_check_does_not_use_system_proxy_discovery(self):
        with patch('urllib.request._opener',None),patch('urllib.request.getproxies',side_effect=AssertionError('Loopback test must be direct')):
            with self.request('/api/health') as r:self.assertEqual(json.load(r)['status'],'ok')

    def test_untrusted_host_and_path_traversal_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/health',headers={'Host':'evil.example'})
        with cm.exception as response:self.assertEqual(response.code,403)
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/../v10/database.py')
        with cm.exception as response:self.assertEqual(response.code,404)

    def test_key_is_memory_only_and_not_returned_in_state(self):
        secret='test-secret-never-real'
        self.request('/api/secrets',{'apiKey':secret}).close()
        with self.request('/api/state') as r:self.assertNotIn(secret,r.read().decode())
        self.assertNotIn(secret,Path(self.s.db.path).read_bytes().decode('utf-8',errors='ignore'))
        self.assertEqual(self.s.secret,secret)

    def test_manually_linked_ppt_can_be_opened(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        rid=self.s.create_run(config(),'linked-file')['id']
        p=Path(self.tmp.name)/'linked_fixture.bin';p.write_bytes(b'test file')
        aid=self.s.db.add_artifact(rid,'linked_ppt',p)
        startfile=Mock()
        with patch('v10.server.os',SimpleNamespace(name='nt',startfile=startfile)):
            with self.request('/api/open-artifact',{'id':aid}) as r:
                self.assertEqual(json.load(r)['status'],'opened')
        startfile.assert_called_once_with(p)

    def test_changed_linked_ppt_is_rejected_before_os_open(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        rid=self.s.create_run(config(),'changed-link')['id']
        p=Path(self.tmp.name)/'linked_fixture.bin';p.write_bytes(b'one')
        aid=self.s.db.add_artifact(rid,'linked_ppt',p);p.write_bytes(b'two')
        startfile=Mock()
        with patch('v10.server.os',SimpleNamespace(name='nt',startfile=startfile)):
            with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/open-artifact',{'id':aid})
            with cm.exception as r:
                self.assertEqual(r.code,409);self.assertEqual(json.load(r)['error'],'FILE_CHANGED')
        startfile.assert_not_called()


if __name__=='__main__':unittest.main()
