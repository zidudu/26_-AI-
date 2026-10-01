"""실제 로컬 저장·분석 검증·캐시 연결. 네이버/API/PowerPoint 경계만 모사합니다."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime
import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from v754 import main_v754 as cli
from v754.core import config, read_json, write_json, digest, V7Error
from v754.analysis_config import load_analysis_config
from v754.analysis_engine import analyze_articles, verify_display_artifact, load_bound_source
from v754.period_config import load_period_config
from v754.period_collect import collect_with_pages
from v754.period_sources import load_collection, load_period_source
from v754.tests.test_analysis import BODY, candidate_for, response_for, FakeAnalyzer
from v754.tests.test_period import snapshot, WEB

# 1x1 PNG. 이미지 파일 경로·해시 연결 검사에만 사용합니다.
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082')


class PeriodPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        write_json(self.root/'config_v5.json', {**WEB,'target_url':'https://cafe.naver.com/iroid/101',
            'profile_dir':'browser_profile','output_dir':'output_v5','browser':'chrome','keywords':['센서'],
            'timeout_seconds':40,'request_interval_seconds':1})
        write_json(self.root/'config_v6.json',{'model':'fixture-model','reasoning_effort':'low','timeout_seconds':120})
        write_json(self.root/'config_v753.json',{'cafe_names':{'20179506':'설정된 카페'},'font_name':'맑은 고딕'})
        self.cfg=config(self.root)
        self.args=SimpleNamespace(command='collect',start='2026-09-16 09:00',end='2026-09-17 09:00',
                                  keywords='센서',prompt=False,run=None)
        self.pages={('센서',1):snapshot('센서',1,[('101','2026.09.16.')])}
        self.fake_page=MagicMock()
        self.source_calls=[];self.rendered=[];self.api=None

    def source(self,page,target,cfg,error,**kwargs):
        self.source_calls.append(target.article_id)
        folder=cfg['capture_output_dir'];(folder/'captures').mkdir(exist_ok=True)
        (folder/'captures/post_001.png').write_bytes(PNG)
        return {'schema_version':'5.0','collector_version':'7.5.4','cafe_id':target.cafe_id,'article_id':target.article_id,
                'title':'EGR 센서 오작동 문제','body':BODY,'body_raw':BODY,'body_sha256':hashlib.sha256(BODY.encode()).hexdigest(),
                'written_at':'2026-09-16T10:00:00+09:00','written_at_raw':'2026.09.16. 10:00',
                'collected_at':'2026-09-17T12:00:00+09:00','url':target.url,'status':'collected',
                'metadata':{'cafe_name':None,'view_count':82,'comment_count':4,
                    'status':{'view_count':'stable_observed','comment_count':'stable_observed'}},
                'metadata_audit':{'warnings':[]},
                'capture':{'status':'captured','source_body_sha256':hashlib.sha256(BODY.encode()).hexdigest(),
                    'files':[{'path':'captures/post_001.png','index':1,'width':1,'height':1,'sha256':hashlib.sha256(PNG).hexdigest()}]}}

    def browser(self,cfg,settings,webcfg,folder,report,checkpoint,window,words):
        with patch('v754.period_collect.load_page',side_effect=lambda page,cfg,k,n,t:deepcopy(self.pages[(k,n)])), \
             patch('v754.period_collect.collect_article',side_effect=self.source):
            return collect_with_pages(cfg,settings,webcfg,folder,report,checkpoint,window,words,
                search_page=self.fake_page,article_page=self.fake_page,timeout_error=TimeoutError)

    def render(self,items,cfg,folder,report,checkpoint,**kwargs):
        for item in items:
            verify_display_artifact(item)
            self.assertTrue(load_period_source(item)['body'])
        self.rendered.extend(deepcopy(items))
        report.update(slides=len(items),pptx=str(folder/'monitoring.pptx'),renderer_execution='mock_only')
        checkpoint()

    def analyze(self,items,spec,folder,report,checkpoint):
        self.api=FakeAnalyzer(response_for(candidate_for(load_period_source(items[0]))))
        return analyze_articles(items,spec,folder,report,checkpoint,analyzer_factory=lambda key,cfg:self.api)

    def patches(self):
        s=ExitStack()
        s.enter_context(patch.object(cli,'ROOT',self.root))
        s.enter_context(patch('v754.period_collect.collect_with_browser',side_effect=self.browser))
        s.enter_context(patch('v754.powerpoint.check_office',return_value='mock'))
        s.enter_context(patch('v754.powerpoint.render',side_effect=self.render))
        s.enter_context(patch('v754.analysis_engine.analyze_articles',side_effect=self.analyze))
        s.enter_context(patch('v754.analysis_engine.load_api_key',return_value=''))
        s.enter_context(redirect_stdout(io.StringIO()))
        return s

    def collect(self):
        with self.patches():
            code=cli.do_collect(self.args,self.cfg)
        folder=next(self.cfg['out_root'].glob('collect_*'))
        return code,folder

    def test_collect_saved_export_and_rebuild_do_not_need_v6_drafts(self):
        original={p.name:p.read_bytes() for p in self.root.glob('config*.json')}
        code,collected=self.collect()
        self.assertEqual(code,0)
        self.assertIsNone(self.api)
        self.assertFalse((self.root/'output_v6').exists())
        data,items=load_collection(collected)
        self.assertTrue(data['collection_complete'])
        self.assertEqual(items[0]['comments'],'4')
        self.assertEqual(items[0]['cafe_name'],'설정된 카페')
        source,old=load_bound_source(items[0]);self.assertEqual(old['monitoring'],{})
        before=(collected/'collection.json').read_bytes()
        self.args.command='export-saved';self.args.run=collected.name
        with self.patches(), patch('v754.period_collect.collect_with_browser',side_effect=AssertionError('no browser on export')):
            self.assertEqual(cli.do_saved_export(self.args,self.cfg),0)
        self.assertEqual(len(self.api.calls),1)
        self.assertEqual(self.source_calls,['101'])
        exported=next(self.cfg['out_root'].glob('export_*'))
        report=read_json(exported/'summary.json')
        self.assertEqual(report['status'],'completed')
        self.assertEqual(report['api_calls'],1)
        self.assertFalse(report['reopened_structure_verified'])
        self.assertNotIn('기존 V6 요약/표시',(exported/'comparison.html').read_text())
        self.assertEqual(self.rendered[0]['source_kind'],'period_collection')
        self.args.run=exported.name
        with self.patches(), patch('v754.analysis_engine.analyze_articles',side_effect=AssertionError('no AI on rebuild')):
            self.assertEqual(cli.rebuild(self.args,self.cfg),0)
        self.assertEqual((collected/'collection.json').read_bytes(),before)
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.root.glob('config*.json')})

    def test_matching_analysis_cache_reused_on_second_export(self):
        _,collected=self.collect();self.args.run=collected.name
        with self.patches():
            cli.do_saved_export(self.args,self.cfg)
            cli.do_saved_export(self.args,self.cfg)
        reports=[read_json(p/'summary.json') for p in self.cfg['out_root'].glob('export_*')]
        self.assertEqual(sum(r['api_calls'] for r in reports),1)
        self.assertEqual(sum(r['analysis_cache_hits'] for r in reports),1)

    def test_zero_period_no_ai_no_ppt(self):
        self.pages={('센서',1):snapshot('센서',1,[],empty=True)}
        self.args.command='collect-export'
        with self.patches(),patch('v754.analysis_engine.analyze_articles',side_effect=AssertionError('no AI for zero')):
            self.assertEqual(cli.do_collect(self.args,self.cfg),0)
        report=read_json(next(self.cfg['out_root'].glob('collect_*/summary.json')))
        self.assertEqual(report['status'],'completed_empty')
        self.assertEqual(report['api_calls'],0)
        self.assertEqual(self.rendered,[])

    def test_incomplete_collection_blocks_ai_and_saved_export(self):
        self.pages[('센서',1)]['pagination_present']=False
        self.args.command='collect-export'
        with self.patches(),patch('v754.analysis_engine.analyze_articles',side_effect=AssertionError('no AI for incomplete')):
            self.assertEqual(cli.do_collect(self.args,self.cfg),2)
        collected=next(self.cfg['out_root'].glob('collect_*'))
        self.assertEqual(read_json(collected/'summary.json')['status'],'collection_incomplete')
        with self.assertRaises(V7Error) as error:load_collection(collected)
        self.assertEqual(error.exception.code,'COLLECTION_INCOMPLETE')

    def test_source_changed_after_collection_rejected(self):
        _,collected=self.collect();_,items=load_collection(collected)
        p=Path(items[0]['refreshed_source_file']);source=read_json(p);source['body']='변경';write_json(p,source)
        with self.assertRaises(V7Error) as error:load_collection(collected)
        self.assertEqual(error.exception.code,'SOURCE_CHANGED')

    def test_tampered_collection_rejected(self):
        _,collected=self.collect();p=collected/'collection.json';data=read_json(p)
        data['window']['end']='2026-09-18T09:00:00+09:00';write_json(p,data)
        with self.assertRaises(V7Error) as error:load_collection(collected)
        self.assertEqual(error.exception.code,'COLLECTION_CHANGED')

    def test_removed_capture_flagged_not_silently_ok(self):
        _,collected=self.collect();_,items=load_collection(collected)
        Path(items[0]['captures'][0]['path']).unlink()
        _,items=load_collection(collected)
        self.assertTrue(items[0]['capture_problem'])

    def test_ppt_failure_preserves_analysis_for_rebuild(self):
        _,collected=self.collect();self.args.run=collected.name
        with self.patches(),patch('v754.powerpoint.render',side_effect=V7Error('PPT_FIXTURE','모사 실패')):
            with self.assertRaises(V7Error):cli.do_saved_export(self.args,self.cfg)
        exported=next(self.cfg['out_root'].glob('export_*'))
        report=read_json(exported/'summary.json')
        self.assertEqual(report['status'],'failed')
        self.assertEqual(len(report['items']),1)
        self.args.run=exported.name
        with self.patches(),patch('v754.analysis_engine.analyze_articles',side_effect=AssertionError('no AI')):
            self.assertEqual(cli.rebuild(self.args,self.cfg),0)

    def test_v754_config_overrides_without_changing_v753(self):
        before=(self.root/'config_v753.json').read_bytes()
        write_json(self.root/'config_v754.json',{'keywords':['고장','센서','고장'],
            'max_pages_per_keyword':20,'period_start':None,'period_end':None,'verify_search_pages':True,
            'model':'another-fixture','cafe_names':{'20179506':'새 표시명'}})
        self.assertEqual(config(self.root)['output_dir'],'output_v754')
        self.assertEqual(config(self.root)['cafe_names']['20179506'],'새 표시명')
        self.assertEqual(load_period_config(self.root)['keywords'],['고장','센서'])
        self.assertEqual(load_analysis_config(self.root)['model'],'another-fixture')
        self.assertEqual((self.root/'config_v753.json').read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
