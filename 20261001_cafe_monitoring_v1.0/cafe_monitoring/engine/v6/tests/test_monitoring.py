"""Practical drafts, immutable receipts, zero-call conversion and image-only handling."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from v6 import engine
from v6.api_client import build_request
from v6.common import atomic_json, digest, read_json, text_hash
from v6.drafts import convert_saved, enrich_source, make_draft
from v6.prompts import make_spec
from v6.schema import validate_analysis, validate_spec
from v6.source import read_article
from v6.tests.test_v6 import WorkspaceTest, FakeAnalyzer, article, response


def draft_analysis(body='경고등이 나타났다가 사라졌다고 합니다.'):
    return {'document_type':'issue_experience','vehicle':{'model':None,'model_year':None,'mileage':None},
            'summary':body,'symptoms':['경고등 표시'],'parts_or_functions':[],'actions':[],
            'focus_relevance':'unclear','evidence_ids':['B0001'],'needs_review':True,'review_reasons':['원인 미상']}


class MonitoringTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        current=patch('v6.engine.make_spec',make_spec)
        current.start();self.addCleanup(current.stop)

    def test_full_body_sent_without_images_and_simpler_required_fields(self):
        a=read_article(self.put(),self.cfg['cafe_id']);spec=make_spec(self.cfg)
        validate_spec(spec)
        r=build_request(a,spec);payload=json.loads(r['input'][0]['content'])
        self.assertEqual(payload['article']['body'],a['body'])
        self.assertNotIn('reported_cause',spec['schema']['properties'])
        self.assertNotIn('image_url',json.dumps(r));self.assertFalse(r['store'])

    def test_known_bad_summary_is_saved_but_never_used_for_display(self):
        source=article(body='뭔가 우다닥 떴는데 이게 뭘까요 주행 어느정도하니까 싹 사라졌습니다,,,,')
        self.put(source);raw=response(content=draft_analysis('주행 중 갑자기 경고등이 떴다고 합니다.'))
        folder,m=engine.start_run(self.cfg,1)
        fake=FakeAnalyzer([raw]);s=engine.execute(self.cfg,folder,m,lambda spec:fake,emit=lambda *a:None)
        self.assertEqual((s['analyzed'],s['failed'],s['drafts_saved']),(1,0,1))
        result=read_json(m['jobs'][0]['result_file']);d=result['draft']
        self.assertTrue(d['monitoring']['summary_suppressed'])
        self.assertEqual(d['monitoring']['display_text'],source['body'])
        self.assertIn('주행 중',d['ai_candidate']['summary'])
        self.assertEqual(d['review']['status'],'not_reviewed')
        self.assertEqual(read_json(engine.receipt_path(folder,m['jobs'][0])),raw)
        new,plan,_=engine.inspect_work(self.cfg,1)
        self.assertFalse(new);self.assertEqual(plan['cached_articles'],1)

    def test_missing_or_invalid_evidence_becomes_review_instead_of_paid_retry(self):
        self.put();folder,m=engine.start_run(self.cfg,1)
        d=draft_analysis();d['evidence_ids']=['B9999']
        fake=FakeAnalyzer([response(content=d)])
        s=engine.execute(self.cfg,folder,m,lambda spec:fake,emit=lambda *a:None)
        self.assertEqual((s['failed'],s['analyzed'],s['api_attempts']),(0,1,1))
        d=read_json(m['jobs'][0]['result_file'])['draft']
        self.assertTrue(d['monitoring']['summary_suppressed'])

    def test_missing_evidence_does_not_discard_otherwise_readable_summary(self):
        a=article();d=draft_analysis();d['evidence_ids']=[]
        value=make_draft(a,validate_analysis(d,a),'6.3',{})
        self.assertFalse(value['monitoring']['summary_suppressed'])
        self.assertTrue(any('원문 번호' in x for x in value['review']['notes']))

    def test_source_with_only_image_uses_zero_api_and_is_cached(self):
        a=article(body='');a.update(content_kind='image_only',media={'image_count':1})
        self.put(a);folder,m=engine.start_run(self.cfg,1)
        with patch('v6.engine.load_api_key',side_effect=AssertionError('No key needed')):
            s=engine.execute(self.cfg,folder,m,emit=lambda *a:None)
        self.assertEqual((s['analyzed'],s['api_attempts'],s['reported_usage']['total_tokens']),(1,0,0))
        result=read_json(m['jobs'][0]['result_file'])
        self.assertFalse(result['draft']['review']['images_analyzed_by_ai'])
        self.assertEqual(result['draft']['provenance']['mode'],'image_only_local')
        self.assertIsNone(result['actual_model'])
        self.assertEqual(engine.inspect_work(self.cfg,1)[1]['cached_articles'],1)
        _, report=convert_saved(self.cfg,m['run_id'])
        self.assertEqual((report['saved'],report['unavailable']),(1,0))

    def test_empty_failed_body_does_not_masquerade_as_image_post(self):
        for media in ({},{'image_count':0},{'image_count':True}):
            a=article(body='');a.update(content_kind='image_only',media=media)
            with self.assertRaises(Exception):read_article(self.put(a),self.cfg['cafe_id'])

    def test_broken_optional_materials_preserve_valid_text(self):
        a=article();a.update(media=True,metadata='bad',capture={'files':[None]})
        source=read_article(self.put(a),self.cfg['cafe_id'])
        self.assertEqual(source['body'],a['body']);self.assertEqual(source['capture']['files'],[])
        self.assertEqual(source['capture']['status'],'partial')

    def test_incomplete_response_is_still_technical_failure(self):
        self.put();f,m=engine.start_run(self.cfg,1);raw=response(content=draft_analysis());raw['status']='incomplete'
        s=engine.execute(self.cfg,f,m,lambda spec:FakeAnalyzer([raw]),emit=lambda *a:None)
        self.assertEqual((s['analyzed'],s['failed']),(0,1))

    def test_request_receipt_recovery_keeps_no_extra_api_calls(self):
        self.put();f,m=engine.start_run(self.cfg,1);j=m['jobs'][0];j.update(status='running',attempts=1)
        engine.save_manifest(f,m);atomic_json(engine.receipt_path(f,j),response(content=draft_analysis()))
        fake=FakeAnalyzer();s=engine.execute(self.cfg,f,m,lambda spec:fake,emit=lambda *a:None)
        self.assertEqual((s['analyzed'],len(fake.ids)),(1,0))
        self.assertTrue(Path(s['review_file']).exists())

    def test_conversion_changes_no_receipts_or_manifests_and_makes_no_api_call(self):
        self.put();f,m=engine.start_run(self.cfg,1);j=m['jobs'][0];j.update(status='failed',attempts=1)
        engine.save_manifest(f,m);atomic_json(engine.receipt_path(f,j),response(content=draft_analysis()))
        before={p:p.read_bytes() for p in f.rglob('*') if p.is_file()}
        with patch('v6.engine.OpenAIAnalyzer',side_effect=AssertionError('No API allowed')):
            path,report=convert_saved(self.cfg,m['run_id'])
        self.assertEqual(report['saved'],1);self.assertEqual(report['api_calls'],0)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertIn('직원 검토 전',path.read_text())

    def test_current_capture_enrichment_cannot_change_old_input(self):
        old=read_article(self.put(),self.cfg['cafe_id']);fresh=copy.deepcopy(old)
        fresh['capture']={'status':'captured','files':[]}
        new=enrich_source(old,[fresh]);self.assertEqual(new['body'],old['body'])
        self.assertEqual(new['capture']['status'],'captured')
        fresh['input_sha256']='changed'
        self.assertNotEqual(enrich_source(old,[fresh])['capture']['status'],'captured')

    def test_html_escapes_untrusted_text(self):
        self.put(article(title='<script>alert(1)</script>'))
        f,m=engine.start_run(self.cfg,1)
        s=engine.execute(self.cfg,f,m,lambda spec:FakeAnalyzer([response(content=draft_analysis())]),emit=lambda *a:None)
        html=Path(s['review_file']).read_text()
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)

    def test_ambiguous_delivery_year_is_empty_in_display_but_original_preserved(self):
        a=article(body='2024년 11월 출고했습니다. 경고등이 떴습니다.')
        d=draft_analysis();d['vehicle']['model_year']='2024년'
        out=make_draft(a,d,'6.3',{})
        self.assertIsNone(out['monitoring']['vehicle']['model_year'])
        self.assertEqual(out['ai_candidate']['vehicle']['model_year'],'2024년')

    def test_unavailable_booking_not_displayed_as_completed(self):
        a=article(body='센서 교체 했는데 경고등 안사라짐. 하이테크 예약풀...')
        d=draft_analysis('하이테크 예약을 완료했다고 합니다.')
        d['actions']=[{'description':'하이테크 예약','status':'completed'}]
        out=make_draft(a,d,'6.3',{})
        self.assertTrue(out['monitoring']['summary_suppressed'])
        self.assertEqual(out['monitoring']['actions'][0]['status'],'unclear')
        self.assertEqual(out['ai_candidate']['actions'][0]['status'],'completed')


if __name__=='__main__':unittest.main()
