"""V7.5.4.1 실사용 실패 자료 회귀. API/COM/브라우저 경계는 모사이며 실측 성공으로 간주하지 않습니다."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import io
import json
import unittest

from v754.analysis_engine import (build_display, analyze_articles, pending_display, verify_display_artifact,
    persist_display, source_identity, mark_summary_pending)
from v754.analysis_schema import validate_candidate, semantic_issues, evidence_numbers
from v754.analysis_prompts import make_spec
from v754.analysis_config import load_analysis_config
from v754.analysis_api import APIError
from v754.summary_repair import SummaryRepair
from v754.summary_layout import logical_text, summary_variants
from v754.collect import collector, metadata as md
from v754.collect.post_capture import snapshot_diff, tile_rectangles, capture_post
from v754.core import DEFAULTS, V7Error, read_json, write_json
from v754.powerpoint import plan_article, plan_article_for_office, text_bounds_fit, verify_analysis_slide
from v754.tests.test_analysis import make_item, candidate_for, response_for, FakeAnalyzer, NEUTRAL
from v754.tests.test_capture_flow import Page
from v754.tests.test_summary_fit import fake_shape, FakeRange
from v754.tests.test_v753_layout import article

FIXTURES = json.loads(Path(__file__).with_name('v7541_regression_fixtures.json').read_text(encoding='utf-8'))['articles']


class SourceRegressionTests(unittest.TestCase):
    def test_all_nine_articles_kept_and_eight_summaries_have_no_summary_warning(self):
        output = []
        for fixture in FIXTURES:
            s = fixture['source']
            a = article(fixture['capture_sizes'], s['article_id'])
            a.update(cafe_id=s['cafe_id'], display_text='', review_notes=[])
            if fixture['candidate']:
                candidate, bindings = validate_candidate(fixture['candidate'], s)
                issues = semantic_issues(candidate, bindings)
                self.assertEqual([i for i in issues if i['field'].startswith('summary')], [], s['article_id'])
                if s['article_id'] == '5498307':
                    self.assertIn('COMPLAINT_UNCLEAR', [i['code'] for i in issues])
                current = build_display(a, s, candidate, bindings, issues)
                self.assertEqual(current['display_text'], ' '.join(c['text'].strip() for c in candidate['summary_claims']))
            else:
                current = pending_display(a, s, 'NO_BODY_FOR_ANALYSIS', '이미지 글')
                self.assertEqual(s['article_id'], '5495607')
                self.assertEqual(len(current['captures']), 2)
                self.assertIn('이미지', current['display_text'])
            self.assertFalse(current['source_mode'])
            pages = plan_article(current, DEFAULTS, text_fits=lambda kind, value: True)
            self.assertTrue(all(p['kind'] == 'captures' and not p['summary_continues'] for p in pages))
            self.assertTrue(all(p['summary'] == current['display_text'] for p in pages))
            output.append(current)
        self.assertEqual(len(output), 9)
        self.assertEqual(sum(len(plan_article(a, DEFAULTS)) for a in output), 9)

    def test_year_equivalence_does_not_equate_durations_or_wrong_century(self):
        self.assertEqual(evidence_numbers('26년 3월'), evidence_numbers('2026년 3월'))
        self.assertEqual(evidence_numbers('23년도'), evidence_numbers('2023년도'))
        self.assertNotEqual(evidence_numbers('26시간'), evidence_numbers('2026시간'))
        self.assertNotEqual(evidence_numbers('1926년'), evidence_numbers('2026년'))
        self.assertNotEqual(evidence_numbers('2024년'), evidence_numbers('2026년'))

    def test_soft_breaks_do_not_drop_characters(self):
        text = '요약을 한 칸에 보존합니다. 공백과 숫자 2026년 3시간을 삭제하지 않습니다. ' * 8
        variants = list(summary_variants(text, 711, 9))
        self.assertTrue(any('\v' in s for s in variants))
        self.assertTrue(all(logical_text(s) == text for s in variants))


class NativeBoundaryRegressionTests(unittest.TestCase):
    def plan_with(self, tr, text):
        shape = fake_shape(); shape.TextFrame.TextRange = tr
        row = SimpleNamespace(Height=68)
        table = SimpleNamespace(Cell=lambda r,c: SimpleNamespace(Shape=shape), Rows=SimpleNamespace(Item=lambda r:row))
        slides = SimpleNamespace(Count=0)
        def delete(): slides.Count -= 1
        slides.Item = lambda i: SimpleNamespace(Delete=delete)
        def make(*args):
            slides.Count += 1
            return SimpleNamespace(Shapes=SimpleNamespace(Item=lambda n:SimpleNamespace(Table=table)))
        a = article([(800, 600)]); a['display_text'] = text
        with patch('v754.powerpoint.make_frame', side_effect=make):
            result = plan_article_for_office(SimpleNamespace(Slides=slides), a, DEFAULTS)
        self.assertEqual(slides.Count, 0)
        return result

    def test_measured_713_over_711_is_not_accepted_as_rounding(self):
        class Overflow(FakeRange):
            @property
            def BoundWidth(self): return 713.75 if self.Text and '\v' not in self.Text else 700
            @property
            def BoundHeight(self): return 40
        s = fake_shape(); s.TextFrame.TextRange = Overflow(); s.TextFrame.TextRange.Text = 'x'
        self.assertFalse(text_bounds_fit(s))
        text = next(f for f in FIXTURES if f['source']['article_id'] == '5490132')
        text = ' '.join(c['text'] for c in text['candidate']['summary_claims'])
        pages = self.plan_with(Overflow(), text)
        self.assertEqual(len(pages), 1)
        self.assertIn('\v', pages[0]['summary'])
        self.assertEqual(logical_text(pages[0]['summary']), text)

    def test_minimum_font_failure_does_not_preempt_full_summary_at_other_sizes(self):
        class NonMonotone(FakeRange):
            @property
            def BoundWidth(self): return 713 if self.Font.Size == 9 else 700
            @property
            def BoundHeight(self): return 40
        pages = self.plan_with(NonMonotone(), '차량 증상을 문의했습니다. ' * 5)
        self.assertEqual(pages[0]['summary_font_size'], 12)
        self.assertEqual(len(pages), 1)

    def test_no_width_or_height_fit_never_creates_continuation(self):
        class Overflow(FakeRange):
            @property
            def BoundWidth(self): return 720
        with self.assertRaises(V7Error) as error:
            self.plan_with(Overflow(), '짧은 요약')
        self.assertEqual(error.exception.code, 'SUMMARY_FIT_REQUIRED')


class CaptureRegressionTests(unittest.TestCase):
    def raw(self, body=None):
        s = next(f['source'] for f in FIXTURES if f['source']['article_id'] == '5490132')
        return {'title':s['title'], 'date':'2026.09.03. 08:29', 'body':s['body'] if body is None else body,
                'body_selector':'.se-main-container', 'body_index':0, 'media':s['media']}

    def test_video_body_and_extra_newlines_can_be_captured(self):
        raw = self.raw(); page = Page({**raw, 'body':raw['body']+'\n\n'})
        a = collector.build_article(raw,collector.Target('20179506','5490132'))
        a['metadata'] = {}
        with TemporaryDirectory() as d, patch.object(md,'settle_metadata'), patch.object(md,'check_after_capture'):
            result = capture_post(page,raw,Path(d),a)
        self.assertEqual(result['status'],'captured',result)
        self.assertEqual(len(result['files']),1)
        self.assertFalse(result['video_played'])
        diff = next(x for x in result['diagnostics'] if x['stage']=='verify_before_capture')
        self.assertEqual(diff['changed_fields'],[])
        self.assertTrue(diff['fields']['body']['raw_changed'])

    def test_real_text_edit_and_missing_extraction_are_not_ignored(self):
        raw=self.raw()
        diff=snapshot_diff(raw,{**raw,'body':raw['body']+' 수리 완료'})
        self.assertEqual(diff['changed_fields'],['body'])
        self.assertNotEqual(diff['fields']['body']['before_sha256'],diff['fields']['body']['after_sha256'])
        self.assertEqual(snapshot_diff(raw,None)['changed_fields'],['title','date','body'])

    def test_thin_tail_is_merged_without_missing_or_overlapping_pixels(self):
        for h in (1200,1201,1220,1319,1320,1321,2401,2530,28801):
            tiles,truncated=tile_rectangles({'x':0,'y':0,'width':800,'height':h})
            self.assertFalse(truncated)
            self.assertEqual(sum(t['height'] for t in tiles),h)
            self.assertTrue(all(t['height']>120 for t in tiles))
            for left,right in zip(tiles,tiles[1:]):self.assertEqual(left['y']+left['height'],right['y'])
        self.assertEqual(len(tile_rectangles({'x':0,'y':0,'width':800,'height':1201})[0]),1)
        self.assertTrue(tile_rectangles({'x':0,'y':0,'width':800,'height':30000})[1])

    def collect(self, raws, captures, window=None):
        page=Page(raws[0]);target=collector.Target('20179506','5490132')
        cfg={'timeout_seconds':5,'cafe_slug':'iroid','capture_output_dir':Path('/unused')}
        if window: cfg['period_window']=window
        with patch.object(collector,'assert_article_access'),patch.object(collector,'assert_target_page'),\
             patch.object(collector,'has_login_redirect',return_value=False),patch.object(collector,'wait_for_article',side_effect=raws),\
             patch.object(md,'initialize_metadata'),patch('v754.collect.post_capture.capture_post',side_effect=captures) as capture,redirect_stdout(io.StringIO()):
            a=collector.collect_article(page,target,cfg,TimeoutError)
        return a,capture

    def test_changed_source_recollected_once_and_final_identity_bound(self):
        raw=self.raw();changed={**raw,'body':raw['body']+' 수정된 글'}
        a,capture=self.collect([raw,changed],[{'status':'failed','files':[],'warnings':['CONTENT_CHANGED_BEFORE_CAPTURE']},
                                            {'status':'captured','files':[{'path':'new.png'}],'warnings':[]}])
        self.assertEqual(capture.call_count,2)
        self.assertEqual(a['body'],changed['body'])
        self.assertEqual(len(a['capture_attempts']),2)
        self.assertEqual(a['capture']['files'][0]['path'],'new.png')

    def test_repeated_change_is_bounded_and_failure_retained(self):
        raw=self.raw();failure={'status':'failed','files':[],'warnings':['CONTENT_CHANGED_BEFORE_CAPTURE']}
        a,capture=self.collect([raw,raw],[deepcopy(failure),deepcopy(failure)])
        self.assertEqual(capture.call_count,2)
        self.assertEqual(a['capture']['files'],[])
        self.assertEqual(len(a['capture_attempts']),2)

    def test_retry_rechecks_date_window(self):
        raw=self.raw();outside={**raw,'date':'2026.08.03. 08:29'}
        from v754.period import Window
        window=Window.from_input('2026-09-01 09:00','2026-09-17 15:35').record()
        a,capture=self.collect([raw,outside],[{'status':'failed','files':[],'warnings':['CONTENT_CHANGED_BEFORE_CAPTURE']}],window)
        self.assertEqual(capture.call_count,1)
        self.assertEqual(a['capture']['reason'],'outside_period')

    def test_video_only_article_is_not_rejected_as_empty(self):
        raw=self.raw('');raw['media']={'image_count':0,'loaded_image_count':0,'video_count':1}
        a=collector.build_article(raw,collector.Target('20179506','5490132'))
        self.assertEqual(a['content_kind'],'video_only')


class RepairBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.item,self.source=make_item(self.root)
        write_json(self.root/'config_v6.json',{'model':'fixture-model'})
        self.spec=make_spec(load_analysis_config(self.root))
        self.candidate=candidate_for(self.source)
        self.folder=self.root/'output_v754'/'run';self.folder.mkdir(parents=True)
        self.report={'api_calls':0, 'analysis_cache_hits':0}
        self.analyzer=FakeAnalyzer(response_for(self.candidate))
        data,bind=validate_candidate(self.candidate,self.source)
        self.current=build_display(self.item,self.source,data,bind,[])
        persist_display(self.current,self.folder/'initial.json',{'identity':source_identity(self.source),'candidate':data})

    def resolver(self, analyzer=None):
        return SummaryRepair(self.spec,self.folder,self.report,lambda:None,analyzer_factory=lambda key,cfg:analyzer or self.analyzer)

    def test_repair_at_most_once_and_receipts_match(self):
        r=self.resolver()
        with patch('v754.summary_repair.load_api_key',return_value=''),redirect_stdout(io.StringIO()):
            self.assertTrue(r(self.current,'SUMMARY_OVERFLOW'))
            self.assertFalse(r(self.current,'SEMANTIC_REVIEW'))
        self.assertEqual(len(self.analyzer.calls),1)
        self.assertEqual(self.report['api_calls'],1)
        verify_display_artifact(self.current)
        self.assertEqual(self.report['api_usage'][0]['total_tokens'],180)
        self.assertTrue((self.folder/'summary_repair_audit.json').exists())

    def test_known_repair_cache_reused_without_more_api(self):
        with patch('v754.summary_repair.load_api_key',return_value=''),redirect_stdout(io.StringIO()):
            self.assertTrue(self.resolver()(self.current,'SUMMARY_OVERFLOW'))
            other=FakeAnalyzer(response_for(self.candidate))
            self.assertTrue(self.resolver(other)(self.current,'SUMMARY_OVERFLOW'))
        self.assertEqual(self.report['api_calls'],1)
        self.assertEqual(other.calls,[])
        verify_display_artifact(self.current)

    def test_uncertain_response_no_retry_and_keeps_original_candidate(self):
        def fail():raise APIError('API_RESULT_UNKNOWN','fixture',True)
        fake=FakeAnalyzer({},fail);r=self.resolver(fake)
        with patch('v754.summary_repair.load_api_key',return_value=''),redirect_stdout(io.StringIO()):
            self.assertFalse(r(self.current,'SUMMARY_OVERFLOW'))
            self.assertFalse(r(self.current,'SUMMARY_OVERFLOW'))
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(self.report['analysis_stop_code'],'API_RESULT_UNKNOWN')
        self.assertEqual(self.report['summary_repairs'][0]['status'],'response_unknown')
        mark_summary_pending(self.current,self.folder,self.report,'SUMMARY_FIT_REQUIRED')
        verify_display_artifact(self.current)
        self.assertEqual(self.current['analysis_candidate'],self.candidate)
        self.assertTrue(self.current['summary_pending'])

    def test_invalid_repair_json_does_not_overwrite_good_candidate(self):
        fake=FakeAnalyzer(response_for({'bad':'data'}));r=self.resolver(fake)
        with patch('v754.summary_repair.load_api_key',return_value=''),redirect_stdout(io.StringIO()):
            self.assertFalse(r(self.current,'SUMMARY_OVERFLOW'))
        self.assertEqual(self.current['analysis_candidate'],self.candidate)
        self.assertEqual(self.report['summary_repairs'][0]['code'],'INVALID_ANALYSIS')
        self.assertEqual(self.report['api_calls'],1)
        verify_display_artifact(self.current)

    def test_placeholder_article_never_calls_summary_api(self):
        pending=pending_display(self.item,self.source,'NO_SAVED_ANALYSIS','없음')
        self.assertFalse(self.resolver()(pending,'SEMANTIC_REVIEW'))
        self.assertEqual(self.analyzer.calls,[])


class MediaArticlePipelineTests(unittest.TestCase):
    def test_textless_image_is_preserved_by_real_analysis_engine_with_zero_api(self):
        from v754.period_sources import build_item
        from v754.period import Window
        from v754.tests.test_capture_flow import png
        import hashlib
        with TemporaryDirectory() as d:
            root=Path(d); folder=root/'run'; folder.mkdir()
            source=deepcopy(next(f['source'] for f in FIXTURES if f['source']['article_id']=='5495607'))
            image=png(800,1201);(root/'image.png').write_bytes(image)
            source['capture']={'status':'captured','files':[{'path':'image.png','index':1,'width':800,'height':1201,'sha256':hashlib.sha256(image).hexdigest()}]}
            path=root/'source.json';write_json(path,source)
            item=build_item(source,path,DEFAULTS,Window.from_input('2026-09-01 09:00','2026-09-17 15:35'))
            write_json(root/'config_v6.json',{'model':'fixture-model'})
            spec=make_spec(load_analysis_config(root));report={}
            with patch('v754.analysis_engine.load_api_key',side_effect=AssertionError('image article must not request API key')),redirect_stdout(io.StringIO()):
                output=analyze_articles([item],spec,folder,report,lambda:None,
                        analyzer_factory=lambda *args:(_ for _ in ()).throw(AssertionError('no analyzer')))
            self.assertEqual(len(output),1)
            self.assertEqual(len(output[0]['captures']),1)
            self.assertEqual(report['api_calls'],0)
            self.assertEqual(report['analysis_results'][0]['status'],'pending_review')
            verify_display_artifact(output[0])
            from v754.comparison import write_comparison
            write_comparison(folder,report)
            self.assertIn('5495607',(folder/'comparison.html').read_text())

    def test_rebuild_migrates_legacy_raw_fallback_and_restores_dropped_image_article(self):
        from v754 import main_v754 as cli
        from v754.period_sources import build_item,save_collection
        from v754.period import Window
        from v754.analysis_engine import seal
        from v754.tests.test_capture_flow import png
        import hashlib
        with TemporaryDirectory() as d:
            root=Path(d);cfg={**DEFAULTS,'out_root':root/'output_v754','project_root':root}
            collected=cfg['out_root']/'collect_old';collected.mkdir(parents=True)
            prior=cfg['out_root']/'export_old';prior.mkdir()
            window=Window.from_input('2026-09-01 09:00','2026-09-17 15:35')
            items=[];old_items=[]
            for fixture in FIXTURES:
                source=deepcopy(fixture['source']);aid=source['article_id']
                source['metadata']={'cafe_name':'시험 카페','view_count':40,'comment_count':1,'status':{'view_count':'stable_observed','comment_count':'stable_observed'}}
                files=[]
                for index,(w,h) in enumerate(fixture['capture_sizes'],1):
                    b=png(w,h);name=f'{aid}_{index}.png';(collected/name).write_bytes(b)
                    files.append({'path':name,'index':index,'width':w,'height':h,'sha256':hashlib.sha256(b).hexdigest()})
                source['capture']={'status':'captured' if files else 'failed','files':files,
                                   'warnings':[] if files else ['CONTENT_CHANGED_BEFORE_CAPTURE']}
                path=collected/f'{aid}.json';write_json(path,source)
                item=build_item(source,path,cfg,window);items.append(item)
                if fixture['candidate']:
                    c,bind=validate_candidate(fixture['candidate'],source)
                    displayed=build_display(item,source,c,bind,semantic_issues(c,bind))
                    if aid in ('5492288','5503639'):
                        displayed.update(source_mode=True,display_text=source['body'])
                    persist_display(displayed,prior/f'{aid}_analysis.json',{'identity':source_identity(source),'candidate':c})
                    old_items.append(displayed)
            report={**cli.report_base('saved_collection_export'), 'items':old_items,'collection_complete':True,
                    'range_search_complete':True,'articles_verified_complete':True,'window':window.record(),
                    'keywords':['SCC','크루즈','계기판'],'cafe_id':'20179506','selected_articles':9,'collection_from':str(collected)}
            save_collection(collected,report,items);write_json(prior/'summary.json',report)
            before={str(p):p.read_bytes() for folder in (collected,prior) for p in folder.iterdir() if p.is_file()}
            outputs=[]
            def render(actual,cfg,folder,report,checkpoint):
                outputs.extend(actual)
                for item in actual:verify_display_artifact(item)
                report['pptx']=str(folder/'mock_only.pptx')
            args=SimpleNamespace(run=prior.name,prompt=False)
            with patch('v754.powerpoint.check_office'),patch('v754.powerpoint.render',side_effect=render),\
                 patch('v754.analysis_engine.Analyzer',side_effect=AssertionError('no API in rebuild')),redirect_stdout(io.StringIO()):
                self.assertEqual(cli.rebuild(args,cfg),2) # captured-missing and image-summary pending stay visible
            self.assertEqual(len(outputs),9)
            for a in outputs:
                self.assertFalse(a['source_mode'])
                if a['id'] in ('5492288','5503639'):self.assertFalse(a['summary_pending'])
            for path,data in before.items():self.assertEqual(Path(path).read_bytes(),data)
            rebuilt=next(cfg['out_root'].glob('rebuild_*'))
            self.assertEqual(read_json(rebuilt/'summary.json')['api_calls'],0)
            self.assertEqual(read_json(rebuilt/'summary.json')['included_articles'],9)
            self.assertIn('5495607',(rebuilt/'comparison.html').read_text())
