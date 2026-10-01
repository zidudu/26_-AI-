"""실제 capture_post 경로의 연결 검사. 브라우저·PNG 응답은 통제한 모사 객체입니다."""
from copy import deepcopy
import hashlib
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from v754.collect import metadata as md
from v754.collect import collector
from v754.collect.post_capture import capture_post
from v754.collect.collector import Target
from .test_metadata import ClockPage, observation


def png(width,height):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    pixels=(b'\0'+b'\xff\xff\xff'*width)*height
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


class Locator:
    def __init__(self,key):self.key=key
    def nth(self,index):return self
    def locator(self,key):return Locator(key)
    def count(self):return 0
    def bounding_box(self,**kwargs):
        return {'x':200,'y':100 if self.key=='h3.title_text' else 130 if self.key=='.article_info .date' else 180,
                'width':800,'height':150 if self.key=='.se-main-container' else 20}


class Frame:
    def __init__(self,raw):self.raw=raw
    def locator(self,key):return Locator(key)
    def evaluate(self,script,arg=None):
        if 'document.documentElement.scrollHeight' in script:return {'h':900,'w':1200,'sh':900,'sw':1200}
        return deepcopy(self.raw)


class Page(ClockPage):
    def __init__(self,raw):
        super().__init__();self.inner=Frame(raw);self.url='https://cafe.naver.com/f-e/cafes/20179506/articles/5482932'
        self.did_capture=False
    def frame(self,name):return self.inner
    def evaluate(self,script,arg=None):return {'x':0,'y':0}
    def screenshot(self,**kwargs):
        self.did_capture=True
        clip=kwargs['clip'];return png(clip['width'],clip['height'])
    def goto(self,*args,**kwargs):return SimpleNamespace(status=200)


class CaptureFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.raw={'title':'EGR 센서 오작동 문제','date':'2026.08.25. 13:11','body':'정비 시간을 문의합니다.',
            'body_selector':'.se-main-container','body_index':0,
            'media':{'image_count':0,'loaded_image_count':0,'video_count':0},
            'metadata':{'comment_count':0,'view_count':78},'document_url':'https://cafe.naver.com/ca-fe/cafes/20179506/articles/5482932'}
        self.page=Page(self.raw)
        self.article=collector.build_article(self.raw,Target('20179506','5482932'))

    def run_capture(self,change_after=False,edit_before=False,fail_screenshot=False):
        def observe(page):return observation(5 if page.did_capture and change_after else 0 if page.time<1 else 4)
        real_settle=md.settle_metadata
        with patch.object(md,'observe',side_effect=observe),patch.object(md,'settle_metadata',
                side_effect=lambda page,article:real_settle(page,article,clock=page.clock)):
            md.initialize_metadata(self.page,self.article)
            if edit_before:self.page.inner.raw={**self.raw,'body':'수정된 원문'}
            if fail_screenshot:
                with patch.object(self.page,'screenshot',side_effect=RuntimeError('controlled error')):
                    result=capture_post(self.page,self.raw,self.root,self.article)
            else:result=capture_post(self.page,self.raw,self.root,self.article)
        return result

    def test_before_capture_count_is_saved_with_new_png_and_after_trace(self):
        result=self.run_capture()
        self.assertEqual(result['status'],'captured',result)
        self.assertEqual(self.article['metadata']['comment_count'],4)
        self.assertEqual(self.article['metadata_audit']['after_capture']['comment_count']['value'],4)
        for item in result['files']:
            self.assertEqual(hashlib.sha256((self.root/item['path']).read_bytes()).hexdigest(),item['sha256'])

    def test_capture_time_change_invalidates_only_changed_metric(self):
        result=self.run_capture(change_after=True)
        self.assertTrue(result['files'])
        self.assertIsNone(self.article['metadata']['comment_count'])
        self.assertEqual(self.article['metadata']['view_count'],80)

    def test_text_edit_before_capture_rejected(self):
        result=self.run_capture(edit_before=True)
        self.assertEqual(result['files'],[])
        self.assertIn('CONTENT_CHANGED_BEFORE_CAPTURE',result['warnings'])

    def test_capture_failure_is_reported_and_no_old_png_used(self):
        result=self.run_capture(fail_screenshot=True)
        self.assertEqual(result['files'],[])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['diagnostics'][-1]['stage'],'screenshot')

    def test_actual_collector_calls_initial_and_capture_metadata_functions(self):
        real_settle=md.settle_metadata
        with patch.object(collector,'assert_article_access'),patch.object(collector,'assert_target_page'),\
             patch.object(collector,'has_login_redirect',return_value=False),\
             patch.object(collector,'wait_for_article',return_value=deepcopy(self.raw)),\
             patch.object(md,'observe',side_effect=lambda p:observation(0 if p.time<1 else 4)),\
             patch.object(md,'settle_metadata',side_effect=lambda p,a:real_settle(p,a,clock=p.clock)):
            a=collector.collect_article(self.page,Target('20179506','5482932'),
                {'timeout_seconds':5,'cafe_slug':'iroid','capture_output_dir':self.root},TimeoutError)
        self.assertEqual(a['metadata']['comment_count'],4)
        self.assertEqual(a['metadata_audit']['initial_collector_metadata']['comment_count'],0)
        self.assertEqual(a['capture']['status'],'captured')

if __name__=='__main__':unittest.main()
