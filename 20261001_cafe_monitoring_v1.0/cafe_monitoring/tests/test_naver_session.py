"""Session handoff/expiry/popups with browser doubles; DPAPI tested on Windows."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v10.common import Problem
from v10.engine import login
from v10.naver_session import SessionStore,CollectionSession,browser_settings,has_session
from v10.service import Service
from v10.settings import defaults
from v9.collection_control import RequestGate


def cookies():
    return [dict(name=name,value='offline-secret-'+name,domain='.naver.com',path='/',
        expires=-1,httpOnly=True,secure=True,sameSite='None') for name in ('NID_AUT','NID_SES')]


class FakeCrypt:
    """Only the Windows encryption boundary is replaced in portable tests."""
    def __init__(self):self.payloads={}
    def CryptProtectData(self,data,description,entropy,reserved,prompt,flags):
        assert flags==1 and reserved is None and prompt is None
        blob=b'offline-sealed:'+hashlib.sha256(data+entropy).digest()
        self.payloads[blob]=(data,entropy);return blob
    def CryptUnprotectData(self,blob,entropy,reserved,prompt,flags):
        data,bound=self.payloads[blob]
        assert bound==entropy and flags==1
        return 'V10 Naver session',data


class Events:
    def __init__(self):self.events={}
    def on(self,event,fn):self.events.setdefault(event,[]).append(fn)
    def remove_listener(self,event,fn):self.events[event].remove(fn)
    def emit(self,event,value):
        for fn in list(self.events.get(event,[])):fn(value)


class Page(Events):
    def __init__(self,url='about:blank'):
        super().__init__();self.main_frame=SimpleNamespace(url=url)


class Context(Events):
    def __init__(self,current=None):
        super().__init__();self.current=deepcopy(current or []);self.pages=[Page()];self.added=[]
    async def cookies(self,*args):return deepcopy(self.current)
    async def add_cookies(self,values):self.added+=deepcopy(values);self.current=deepcopy(values)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(self.tmp.name,launch=False);self.db=self.service.db
        cfg=defaults();cfg.update(sendMail=False,stepAnalyze=False,stepPpt=False)
        self.rid=self.service.create_run(cfg,'session')['id']
        self.cfg=browser_settings(self.db);self.store=SessionStore(self.db,self.cfg)
        self.crypt=FakeCrypt();self.patch=patch.dict(sys.modules,{'win32crypt':self.crypt})
        self.patch.start();self.addCleanup(self.patch.stop)

    def guard(self,ctx):return CollectionSession(self.db,self.rid,ctx,self.cfg,RequestGate(0))

    def test_login_then_closed_browser_restores_session_in_collection(self):
        launches=[];current=[]
        class LoginPage:
            def goto(page,*args,**kwargs):current[:]=cookies()
            def wait_for_timeout(page,_):context.pages.clear();current.clear()
        context=SimpleNamespace(pages=[LoginPage()],cookies=lambda:deepcopy(current),close=lambda:None)
        def launch(**opts):launches.append(opts);return context
        @contextmanager
        def playwright():yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        with patch.dict(sys.modules,{'playwright':SimpleNamespace(),'playwright.sync_api':SimpleNamespace(sync_playwright=playwright)}):login(self.db)
        self.assertEqual(current,[])
        self.assertEqual(self.db.get('naver_auth')['state'],'session_present')
        self.assertEqual(launches[0]['user_data_dir'],str(self.cfg['profile_dir']))
        ctx=Context()
        async def collect():
            async with self.guard(ctx):self.assertTrue(has_session(await ctx.cookies()))
        asyncio.run(collect())
        self.assertEqual(ctx.added,cookies())
        self.assertTrue(all(c['expires']==-1 for c in ctx.added))

    def test_missing_session_rejects_before_collection_and_clears_green_state(self):
        self.db.put('naver_auth',{'state':'verified','checkedAt':'earlier'})
        ctx=Context();guard=self.guard(ctx)
        async def collect():
            with self.assertRaisesRegex(Problem,'로그인'):
                async with guard:self.fail('Collection must not start')
        asyncio.run(collect())
        self.assertEqual(self.db.get('naver_auth')['state'],'unverified')
        self.assertEqual(guard.gate.reason,'LOGIN_REQUIRED')
        self.assertFalse(ctx.events['page'])

    def test_cancel_request_closes_login_context_without_creating_green_state(self):
        closed=[]
        class LoginPage:
            def goto(page,*args,**kwargs):pass
            def wait_for_timeout(page,_):self.db.put('naver_login_cancelled',True)
        context=SimpleNamespace(pages=[LoginPage()],cookies=lambda:[],close=lambda:closed.append(True))
        @contextmanager
        def playwright():yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda **_:context))
        with patch.dict(sys.modules,{'playwright':SimpleNamespace(),'playwright.sync_api':SimpleNamespace(sync_playwright=playwright)}):login(self.db)
        self.assertEqual(closed,[True])
        self.assertEqual(self.db.get('naver_auth')['state'],'unverified')
        self.assertFalse(self.store.path.exists())

    def test_login_popup_stops_requests_and_invalidates_saved_session(self):
        self.store.save(cookies());ctx=Context(cookies());guard=self.guard(ctx)
        async def collect():
            async with guard:
                popup=Page();ctx.emit('page',popup)
                popup.main_frame.url='https://nid.naver.com/nidlogin.login?url=private'
                popup.emit('framenavigated',popup.main_frame)
                self.assertTrue(guard.rejected)
                with self.assertRaises(Exception):guard.gate.check()
        asyncio.run(collect())
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.db.get('naver_auth')['state'],'unverified')

    def test_public_pages_and_non_naver_frames_do_not_invalidate_session(self):
        ctx=Context(cookies());guard=self.guard(ctx)
        async def collect():
            async with guard:
                page=ctx.pages[0];page.main_frame.url='https://cafe.naver.com/iroid'
                page.emit('framenavigated',page.main_frame)
                page.emit('framenavigated',SimpleNamespace(url='https://ads.example/nidlogin'))
                self.assertFalse(guard.rejected)
        asyncio.run(collect())
        self.assertTrue(self.store.path.exists())

    def test_live_profile_session_takes_priority_over_saved_session(self):
        self.store.save(cookies());new=cookies();new[0]['value']='offline-new-login'
        ctx=Context(new)
        async def collect():
            async with self.guard(ctx):self.assertEqual(ctx.current,new)
        asyncio.run(collect())
        self.assertEqual(ctx.added,[]);self.assertEqual(self.store.load(),new)

    def test_cache_is_bound_to_profile_and_filters_non_naver_cookies(self):
        values=cookies()+[dict(name='other',value='offline-other-secret',domain='notnaver.com',expires=-1)]
        self.store.save(values)
        self.assertNotIn(b'offline-secret',self.store.path.read_bytes())
        self.assertEqual(self.store.load(),cookies())
        other=SessionStore(self.db,{**self.cfg,'profile_dir':Path(self.tmp.name)/'another_profile'})
        self.assertEqual(other.load(),[])
        other.path.write_bytes(self.store.path.read_bytes())
        with self.assertRaisesRegex(Problem,'불러오지'):other.load()

    def test_expired_session_is_never_extended_or_reused(self):
        with patch('v10.naver_session.time.time',return_value=100):
            values=cookies()
            for c in values:c['expires']=200
            self.store.save(values)
        with patch('v10.naver_session.time.time',return_value=201):
            self.assertEqual(self.store.load(),[])
            self.assertFalse(has_session(values))

    def test_cookies_never_appear_in_db_state_or_logs(self):
        ctx=Context(cookies())
        async def collect():
            async with self.guard(ctx):pass
        asyncio.run(collect())
        public=json.dumps(self.service.snapshot())+json.dumps(self.db.logs(self.rid))
        for c in cookies():
            self.assertNotIn(c['value'],public)
            self.assertNotIn(c['value'].encode(),self.db.path.read_bytes())

    def test_encryption_failure_never_writes_plaintext(self):
        with patch.object(self.crypt,'CryptProtectData',side_effect=RuntimeError('offline-secret')):
            with self.assertRaises(Problem) as error:self.store.save(cookies())
        self.assertNotIn('offline-secret',str(error.exception))
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.path.with_suffix('.tmp').exists())

    @unittest.skipUnless(os.name=='nt','Actual Windows DPAPI requires Windows')
    def test_windows_dpapi_round_trip(self):
        self.patch.stop()
        self.store.save(cookies())
        self.assertNotIn(b'offline-secret',self.store.path.read_bytes())
        self.assertEqual(self.store.load(),cookies())


if __name__=='__main__':unittest.main()
