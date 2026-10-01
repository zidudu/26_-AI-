"""Real V9 adapter/configuration contracts, without browser, AI or Office calls."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import asyncio
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v10.common import ENGINE,KST,read_json,write_json
from v10.database import Database
from v10.engine import LegacyEngine
from v10.settings import defaults
from v10.service import Service
from v10.naver_session import browser_settings


class EngineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.folder=self.root/'run';self.folder.mkdir()
        self.db=Database(self.root/'data');self.cfg=defaults()
        self.cfg.update(stepAnalyze=False,stepPpt=False,sendMail=False)

    def make_engine(self):
        # Stub only the platform gate, not Backend or config readers.
        with patch('v10.engine.os',SimpleNamespace(name='nt')):
            return LegacyEngine(self.db,'offline-contract',self.cfg,self.folder)

    def legacy(self,interval=None):
        root=self.root/'legacy';root.mkdir()
        data=read_json(ENGINE/'config_v5.json')
        data.pop('request_interval_seconds',None)
        if interval is not None:data['request_interval_seconds']=interval
        write_json(root/'config_v5.json',data);self.db.put('legacy_root',str(root))
        return root

    def test_real_adapter_initializes_from_bundled_settings(self):
        engine=self.make_engine()
        self.assertTrue((self.folder/'engine_config/config_v5.json').is_file())
        self.assertEqual(engine.backend.words,self.cfg['keywords'])
        self.assertEqual(engine.backend.webcfg['profile_dir'],self.db.directory/'browser_profile_chrome')
        report=engine.report([],[])
        self.assertTrue(report['collection_complete'])
        self.assertEqual(report['dashboard_stats']['total_posts'],0)

    def test_legacy_optional_interval_defaults_without_editing_original(self):
        root=self.legacy();before=(root/'config_v5.json').read_bytes()
        engine=self.make_engine()
        self.assertEqual(engine.backend.webcfg['request_interval_seconds'],2)
        self.assertEqual(engine.backend.webcfg['profile_dir'],root/'data/browser_profile_chrome')
        self.assertEqual((root/'config_v5.json').read_bytes(),before)

    def test_legacy_invalid_request_interval_is_rejected(self):
        from v754.core import V7Error
        self.legacy(interval=0)
        with self.assertRaisesRegex(V7Error,'request_interval_seconds'):self.make_engine()

    def test_saved_collection_passes_real_adapter_source_checks(self):
        from v754.period import Window
        from v754.period_sources import build_item,save_collection
        engine=self.make_engine();cafe=deepcopy(self.cfg['catalog']['cafes'][0])
        cafe['naverCafeId']='111'
        # Exercise the real V10 period calculation, not hand-picked minute input.
        bounds=Service(self.db.directory,launch=False).period(
            self.cfg,datetime(2026,9,30,13,29,54,123456,tzinfo=KST))
        self.db.create_run('offline-contract',self.cfg,bounds,request_id='adapter-collection')
        @asynccontextmanager
        async def browser():
            engine.backend.context=SimpleNamespace(pages=[],on=Mock(),remove_listener=Mock(),
                cookies=AsyncMock(return_value=[dict(name=n,value='offline-cookie',domain='.naver.com',expires=-1) for n in ('NID_AUT','NID_SES')]))
            yield engine.backend
        async def collect(row,folder,start,end):
            source=dict(cafe_id='111',article_id='7',title='후방 카메라',body='시험 원문',body_raw='시험 원문',
                written_at='2026-09-30T10:00:00+09:00',collected_at='2026-09-30T13:29:54+09:00',
                url='https://cafe.naver.com/bestcm/7',media={})
            path=folder/'source.json';write_json(path,source)
            window=Window(start,end);item=build_item(source,path,engine.cfg,window)
            item['matched_keywords']=['후방']
            report=dict(window=window.record(),keywords=engine.backend.words,cafe_id='111',
                range_search_complete=True,articles_verified_complete=True,collection_complete=True)
            save_collection(folder,report,[item])
        observations=[]
        with patch.object(engine.backend,'async_browser',browser),patch.object(engine.backend,'collect_async',collect),patch('v10.naver_session.SessionStore.save'):
            asyncio.run(engine.collect([cafe],bounds,lambda *args:observations.append(args)))
        self.assertEqual(len(observations),1)
        _,items,sources,error=observations[0]
        self.assertIsNone(error)
        self.assertEqual(items[0]['id'],'7')
        self.assertEqual(sources[0]['body_raw'],'시험 원문')

    def test_login_and_collection_choose_same_profile_browser_and_lock(self):
        for legacy in (False,True):
            with self.subTest(legacy=legacy):
                if legacy:
                    root=self.legacy();data=read_json(root/'config_v5.json')
                    data.update(browser='msedge',profile_dir='data/custom_profile')
                    write_json(root/'config_v5.json',data)
                engine=self.make_engine();login_cfg=browser_settings(self.db)
                for key in ('profile_dir','profile_lock','browser'):
                    self.assertEqual(engine.backend.webcfg[key],login_cfg[key])

    def test_actual_collection_disables_office_capture_preparation_when_ppt_off(self):
        import base64
        from v9.ppt_images import save_capture,finalize_capture_images_async
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=')
        for enabled in (False,True):
            with self.subTest(ppt=enabled):
                self.cfg['stepPpt']=enabled;engine=self.make_engine()
                folder=self.folder/str(enabled);folder.mkdir()
                engine.backend.context=SimpleNamespace(new_page=AsyncMock(return_value=SimpleNamespace(close=AsyncMock())))
                engine.backend.timeout_error=TimeoutError
                seen=[]
                async def capture(cfg,settings,webcfg,output,report,*args,**kwargs):
                    ppi=webcfg['ppt_image_ppi'];seen.append(ppi)
                    entry=save_capture(png,output,Path('.'),1,{},ppi)
                    await finalize_capture_images_async([entry],{1:png},output,ppi,2)
                    self.assertEqual((output/'post_001.png').read_bytes(),png)
                    report.update(collection_complete=True,items=[])
                with patch('v9.async_collect.period_collect.collect_with_pages',capture),patch('v9.ppt_measurements.measure_originals',side_effect=RuntimeError('Offline Office boundary')) as office:
                    asyncio.run(engine.backend.collect_async(dict(club_id='111',slug='bestcm',name='GN7'),folder,
                        datetime(2026,9,29,0,0,tzinfo=KST),datetime(2026,9,30,0,0,tzinfo=KST)))
                self.assertEqual(seen,[125 if enabled else 0])
                self.assertEqual(office.call_count,1 if enabled else 0)


if __name__=='__main__':unittest.main()
