from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v10.common import Problem
from v10.service import Service


class NaverLoginFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=Service(self.tmp.name,launch=False)
        self.child=SimpleNamespace(returncode=None)
        self.child.poll=lambda:self.child.returncode

    def spawn(self):self.s.child=self.child;self.s.child_run=None

    def test_saved_login_requires_confirmation_and_cancel_keeps_session(self):
        for state in ('session_present','verified'):
            auth={'state':state,'checkedAt':'2026-09-30T15:37:23+09:00'}
            self.s.db.put('naver_auth',auth)
            with patch('v10.service.os',SimpleNamespace(name='nt')),patch.object(self.s,'spawn') as spawn:
                result=self.s.login()
                self.assertTrue(result['needsConfirmation']);spawn.assert_not_called()
                self.s.cancel_login()
                self.assertEqual(self.s.db.get('naver_auth'),auth)
                self.s.login(confirm=True);spawn.assert_called_once()

    def test_login_done_requires_closed_browser_and_saved_session(self):
        self.spawn();self.s.db.put('naver_auth',{'state':'session_present','checkedAt':'saved'})
        with self.assertRaises(Problem) as cm:self.s.confirm_login()
        self.assertEqual(cm.exception.code,'NAVER_BROWSER_OPEN')
        self.child.returncode=0
        self.assertEqual(self.s.confirm_login()['naverAuth']['state'],'session_present')
        self.s.db.put('naver_auth',{'state':'unverified','checkedAt':None})
        with self.assertRaises(Problem) as cm:self.s.confirm_login()
        self.assertEqual(cm.exception.code,'NAVER_LOGIN_REQUIRED')
        self.assertEqual(self.s.db.get('naver_auth')['state'],'unverified')

    def test_duplicate_start_and_cancel_only_target_login_worker(self):
        with patch('v10.service.os',SimpleNamespace(name='nt')),patch.object(self.s,'spawn',side_effect=self.spawn) as spawn:
            self.s.login();self.s.login()
            spawn.assert_called_once()
            self.assertTrue(self.s.naver_auth()['browserOpen'])
            self.s.cancel_login();self.assertTrue(self.s.db.get('naver_login_cancelled'))
        self.s.db.put('naver_login_cancelled',False)
        self.s.child_run='collecting'
        self.s.cancel_login()
        self.assertFalse(self.s.db.get('naver_login_cancelled'))


if __name__=='__main__':unittest.main()
