"""브라우저의 화면 전환을 모의하여 조기 건너뜀과 인증 중단을 검증합니다."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import collector
import page_guard


class GuardTests(unittest.TestCase):
    def page(self, states):
        frame=MagicMock()
        frame.url='https://cafe.naver.com/ca-fe/cafes/20179506/articles/5497069'
        frame.evaluate.side_effect=states
        page=MagicMock()
        page.frames=[frame]
        page.frame.return_value=frame
        return page,frame

    def test_grade_appearing_after_loading_skips_after_one_poll(self):
        page,frame=self.page([{'ready':False},{'grade':True}])
        with patch.object(collector.time,'monotonic',side_effect=[0,0.1]):
            with self.assertRaises(collector.CollectorError) as err:
                collector.wait_for_article(page,{'timeout_seconds':40})
        self.assertEqual(err.exception.code,'GRADE_REQUIRED')
        self.assertEqual(err.exception.details,{'detection':'grade'})
        page.wait_for_timeout.assert_called_once_with(250)
        self.assertEqual(frame.evaluate.call_count,2)

    def test_ready_article_extracted_without_fixed_wait(self):
        raw={'title':'글','date':'2026.09.11. 12:00','body':'실제 본문','document_url':'https://cafe.naver.com/ArticleRead.nhn?articleid=1&clubid=20179506'}
        page,_=self.page([{'ready':True,'title_visible':True,'date_visible':True,'body_visible':True},raw])
        self.assertEqual(collector.wait_for_article(page,{'timeout_seconds':40}),raw)
        page.wait_for_timeout.assert_not_called()

    def test_grade_detected_before_body_wait_starts(self):
        page,_=self.page([{'grade':True}])
        page.goto.return_value.status=200
        with patch.object(collector,'wait_for_article') as waiting:
            with self.assertRaises(collector.CollectorError) as err:
                collector.collect_article(page,collector.Target('20179506','5497069'),{'timeout_seconds':40,'cafe_slug':'iroid'},TimeoutError)
        self.assertEqual(err.exception.code,'GRADE_REQUIRED')
        waiting.assert_not_called()

    def test_unknown_blank_page_remains_failure_with_diagnostics(self):
        page,_=self.page([{'ready':False,'title_visible':False,'body_visible':False}])
        with patch.object(collector.time,'monotonic',side_effect=[0,41]):
            with self.assertRaises(collector.CollectorError) as err:
                collector.wait_for_article(page,{'timeout_seconds':40})
        self.assertEqual(err.exception.code,'PAGE_NOT_READY')
        self.assertTrue(err.exception.details['frame_detected'])
        self.assertFalse(err.exception.details['body_visible'])
        self.assertNotIn('grade',err.exception.details)

    def test_session_challenge_overrides_article_skip(self):
        page,_=self.page([{'grade':True,'captcha':True}])
        with self.assertRaises(collector.CollectorError) as err: page_guard.assert_article_access(page)
        self.assertEqual(err.exception.code,'AUTH_REQUIRED')

    def test_each_explicit_access_reason_has_distinct_code(self):
        for field,expected in [('grade','GRADE_REQUIRED'),('membership','MEMBERSHIP_REQUIRED'),('deleted','ARTICLE_DELETED'),('denied','ACCESS_DENIED')]:
            with self.subTest(field=field):
                page,_=self.page([{field:True}])
                with self.assertRaises(collector.CollectorError) as err: page_guard.assert_article_access(page)
                self.assertEqual(err.exception.code,expected)

    def test_only_known_naver_frames_are_read(self):
        page,frame=self.page([{'ready':False}])
        external=MagicMock(); external.url='https://ad.example.invalid/'
        page.frames.append(external)
        self.assertEqual(len(page_guard.read_states(page)),1)
        external.evaluate.assert_not_called()

    def test_transient_frame_replacement_is_rechecked(self):
        page,frame=self.page([RuntimeError('Execution context was destroyed'),{'grade':True}])
        with patch.object(collector.time,'monotonic',side_effect=[0,0.1]):
            with self.assertRaises(collector.CollectorError) as err: collector.wait_for_article(page,{'timeout_seconds':40})
        self.assertEqual(err.exception.code,'GRADE_REQUIRED')
        page.wait_for_timeout.assert_called_once()

    def test_unexpected_browser_error_is_not_hidden_as_grade(self):
        page,_=self.page([RuntimeError('browser disconnected')])
        with self.assertRaises(RuntimeError): page_guard.assert_article_access(page)

    def test_login_redirect_is_detected_without_form_values(self):
        page=SimpleNamespace(frames=[SimpleNamespace(url='https://nid.naver.com/nidlogin.login')])
        with self.assertRaises(collector.CollectorError) as err: page_guard.check_states(page,[])
        self.assertEqual(err.exception.code,'LOGIN_REQUIRED')

if __name__=='__main__': unittest.main()
