"""Opt-in real Chromium tests on synthetic DOM; all network requests are intercepted."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import collector
from history import valid_article
from post_capture import tile_rectangles, capture_post


class CaptureGeometryTests(unittest.TestCase):
    def test_tiles_cover_every_row_once_and_mark_overflow(self):
        tiles,limited=tile_rectangles({'x':10,'y':50,'width':800,'height':3101})
        self.assertEqual(sum(t['height'] for t in tiles),3101)
        self.assertEqual([t['y'] for t in tiles],[50,1250,2450]);self.assertFalse(limited)
        tiles,limited=tile_rectangles({'x':10,'y':50,'width':800,'height':50000})
        self.assertTrue(limited);self.assertEqual(len(tiles),24)


@unittest.skipUnless(os.environ.get('CAFE_TEST_BROWSER'), 'set CAFE_TEST_BROWSER for offline Chromium DOM tests')
class RealBrowserCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright,TimeoutError
        cls.timeout=TimeoutError
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(executable_path=os.environ['CAFE_TEST_BROWSER'],headless=True,
            args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.page=self.browser.new_page(viewport={'width':1365,'height':900})
        self.addCleanup(self.page.close)
        self.target=collector.Target('20179506','9000001')
        self.cfg={'cafe_id':'20179506','cafe_slug':'test','timeout_seconds':5,'capture_output_dir':self.folder}

    def site(self,legacy=False,image_only=False,broken=False,grade=False,long=True,video=False):
        svg='<svg xmlns="http://www.w3.org/2000/svg" width="700" height="400"><rect width="700" height="400" fill="#28aa66"/><text x="30" y="80" font-size="38">POST IMAGE</text></svg>'
        src='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()
        if broken:src='https://cafe.naver.com/broken.png'
        text='' if image_only else '<p>경고등이 떴습니다. 원인은 미상입니다. 센터 방문 예정입니다.</p>'
        tail='<div style="height:1600px;background:#f8f0db">긴 게시글 중간 내용</div>' if long and not image_only else ''
        body=f'{text}<img src="{src}" width="700" height="400">{tail}<p style="height:60px;background:#e1ad37">'+('' if image_only else '본문 마지막 줄')+'</p>'
        if video:
            body += '<div class="se-component se-video"><div class="se-module-video" style="height:100px;background:rgb(11,42,99)"></div><p class="se-caption">작성자 영상 설명: 재생 00:14 부분을 보세요.</p></div>'
        selector='ContentRenderer' if legacy else 'se-main-container'
        inner=f'''<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:white;font-family:sans-serif}}h3{{margin:12px 0}}.article_viewer{{margin:20px;width:750px}}img{{display:block}}p{{margin:10px 0}}</style>
<div class="article_viewer"><h3 class="title_text">캡처 시험용 경고등 게시글</h3><div class="article_info"><span class="date">2026.09.12. 12:00</span><span class="count">조회 1,234</span></div>
<div class="button_comment"><span class="num">11</span></div><div class="{selector}">{body}</div></div>
<div style="background:#f00;height:100px">댓글과 하단 광고는 캡처 제외</div>'''
        if grade:inner='<h3>질문있어요</h3><p>싼타페 등급이 되시면 읽기가 가능한 게시판 입니다.</p>'
        height=2700 if long and not image_only else 900
        outer=f'''<!doctype html><meta charset="utf-8"><div class="cafe_name">합성 카페</div><div style="position:absolute;width:190px;height:4000px;background:#f00">SIDE MENU</div>
<iframe name="cafe_main" id="cafe_main" style="border:0;margin-left:200px;width:900px;height:{height}px" src="https://cafe.naver.com/ArticleRead.nhn?clubid=20179506&articleid=9000001"></iframe>'''
        def route(r):
            url=r.request.url
            if url.endswith('broken.png'):r.fulfill(status=404,body='missing')
            else:r.fulfill(status=200,content_type='text/html',body=inner if 'ArticleRead.nhn' in url else outer)
        self.page.route('**/*',route)

    def collect(self):
        return collector.collect_article(self.page,self.target,self.cfg,self.timeout)

    def test_long_modern_post_real_pngs_exclude_side_menu_and_comments(self):
        self.site();a=self.collect()
        self.assertEqual(a['capture']['status'],'captured',a['capture'])
        self.assertGreaterEqual(len(a['capture']['files']),2)
        self.assertEqual(a['metadata']['view_count'],1234);self.assertEqual(a['metadata']['comment_count'],11)
        self.assertEqual(a['metadata']['cafe_name'],'합성 카페')
        from PIL import Image
        found_image=False
        for f in a['capture']['files']:
            im=Image.open(self.folder/f['path']).convert('RGB')
            colors=set(im.getdata())
            self.assertNotIn((255,0,0),colors)
            found_image=found_image or (40,170,102) in colors
        self.assertTrue(found_image)
        self.assertIn('본문 마지막 줄',a['body'])

    def test_legacy_image_only_is_collected_and_valid_for_history(self):
        self.site(legacy=True,image_only=True,long=False);a=self.collect()
        self.assertEqual(a['body'],'');self.assertEqual(a['content_kind'],'image_only')
        self.assertEqual(a['capture']['status'],'captured',a['capture'])
        path=collector.save_article(self.folder,a)
        self.assertEqual(valid_article(path,self.cfg)['media']['image_count'],1)

    def test_broken_inline_image_is_partial_capture_with_text_preserved(self):
        self.site(broken=True,long=False);a=self.collect()
        self.assertTrue(a['body']);self.assertEqual(a['capture']['status'],'partial')
        self.assertIn('IMAGE_NOT_LOADED',a['capture']['warnings'])

    def test_screenshot_failure_does_not_fail_body_collection(self):
        self.site(long=False)
        with patch.object(self.page,'screenshot',side_effect=RuntimeError('fixture screenshot failure')):
            a=self.collect()
        self.assertEqual(a['status'],'collected');self.assertEqual(a['capture']['status'],'failed')
        self.assertTrue(a['body'])
        self.assertIn('CAPTURE_STEP_FAILED',a['capture']['warnings'])
        self.assertEqual(a['capture']['diagnostics'][-1]['stage'],'screenshot')
        self.assertEqual(a['capture']['diagnostics'][-1]['exception_type'],'RuntimeError')
        self.assertNotIn('fixture screenshot failure',json.dumps(a['capture']))

    def test_late_video_controls_do_not_change_body_or_cancel_screenshot(self):
        self.site(long=False,video=True)
        def after_initial_read(page,raw,folder,article):
            page.frame(name='cafe_main').evaluate('''() => {
                document.querySelector('.se-module-video').innerHTML = '<div class="prismplayer-area"><div class="pzp"><video></video>100%<br>재생<br>56<br>00:14<br>플레이어 제목</div></div>';
                const meta=document.createElement('div');meta.className='se-media-meta';
                meta.innerHTML='<strong class="se-media-meta-info-title">동영상 제목</strong>';
                document.querySelector('.se-video').appendChild(meta);
            }''')
            return capture_post(page,raw,folder,article)
        with patch('post_capture.capture_post',side_effect=after_initial_read):a=self.collect()
        self.assertEqual(a['capture']['status'],'captured',a['capture'])
        self.assertIn('작성자 영상 설명: 재생 00:14 부분을 보세요.',a['body'])
        self.assertNotIn('100%',a['body']);self.assertNotIn('플레이어 제목',a['body'])
        self.assertNotIn('동영상 제목',a['body']);self.assertEqual(a['media']['video_count'],1)
        self.assertEqual(self.page.frame(name='cafe_main').locator('.pzp').count(),1)
        from PIL import Image
        self.assertTrue(any((11,42,99) in set(Image.open(self.folder/f['path']).convert('RGB').getdata())
                            for f in a['capture']['files']))

    def test_real_author_edit_before_capture_is_still_rejected(self):
        self.site(long=False,video=True)
        def edit(page,raw,folder,article):
            page.frame(name='cafe_main').evaluate("() => document.querySelector('.se-caption').textContent = '작성자가 바꾼 설명'")
            return capture_post(page,raw,folder,article)
        with patch('post_capture.capture_post',side_effect=edit):a=self.collect()
        self.assertEqual(a['capture']['status'],'failed')
        self.assertEqual(a['capture']['files'],[])
        self.assertIn('CONTENT_CHANGED_BEFORE_CAPTURE',a['capture']['warnings'])
        self.assertEqual(a['capture']['diagnostics'][-1]['stage'],'verify_before_capture')

    def test_author_edit_during_screenshot_unlinks_capture(self):
        self.site(long=False)
        screenshot=self.page.screenshot
        def edit(**kwargs):
            png=screenshot(**kwargs)
            self.page.frame(name='cafe_main').evaluate("() => document.querySelector('.se-main-container p').textContent = '수정된 본문'")
            return png
        with patch.object(self.page,'screenshot',side_effect=edit):a=self.collect()
        self.assertEqual(a['capture']['files'],[])
        self.assertIn('CONTENT_CHANGED_DURING_CAPTURE',a['capture']['warnings'])

    def test_legacy_inline_words_and_video_caption_survive_filtering(self):
        self.site(legacy=True,long=False,video=True)
        self.page.goto(self.target.url)
        f=self.page.frame(name='cafe_main')
        f.wait_for_selector('.ContentRenderer')
        f.evaluate('''() => {
            const b=document.querySelector('.ContentRenderer');
            b.innerHTML='<p>앞 <b>강조</b> 뒤</p><div class="se-module-video">재생 100%</div><p>실제 설명<br>다음 줄</p>';
        }''')
        raw=f.evaluate(collector.body_extraction_script(),{'selector':'.article_viewer .ContentRenderer','index':0})
        self.assertEqual(raw['body'],'앞 강조 뒤\n\n실제 설명\n다음 줄')

    def test_screenshot_timeout_reports_its_stage(self):
        self.site(long=False)
        with patch.object(self.page,'screenshot',side_effect=self.timeout('fixture timeout')):a=self.collect()
        self.assertIn('CAPTURE_TIMED_OUT',a['capture']['warnings'])
        self.assertEqual(a['capture']['diagnostics'][-1]['stage'],'screenshot')

    def test_grade_page_does_not_capture_as_article(self):
        self.site(grade=True)
        with self.assertRaises(collector.CollectorError) as caught:self.collect()
        self.assertEqual(caught.exception.code,'GRADE_REQUIRED')
        self.assertFalse(list(self.folder.rglob('*.png')))


if __name__=='__main__':unittest.main()
