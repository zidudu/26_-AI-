from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from v8.configuration import V8Error, read_json, write_json
from v8.schedule import scope
from v9.run_state import load_state, commit_state, get_record
from v9.tests.test_pipeline import config


class RunStateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root/'output_v9/state.json'
        self.cfg = config()
        self.words = ['SCC','경고등']

    def load(self):
        with redirect_stdout(StringIO()):
            return load_state(self.root, self.cfg, self.words, self.path)

    def test_old_v8_cursor_migration_is_preserved_without_rewriting_old_state(self):
        self.cfg['inherit_v8_cursor'] = True
        old = self.root/'output_v8/state.json'
        write_json(old, {'scope':scope('20179506',self.words), 'active':None,
                         'cursor':'2026-09-22T09:00:00+09:00'})
        before = old.read_bytes()
        state = self.load()
        self.assertEqual(state['cafes']['iroid']['cursor'], '2026-09-22T09:00:00+09:00')
        self.assertEqual(old.read_bytes(), before)

    def test_v8_unknown_mail_blocks_migration(self):
        self.cfg['inherit_v8_cursor'] = True
        identifier = 'run_20260921_0900_20260922_0900_abcdef012345'
        write_json(self.root/'output_v8/state.json', {'scope':scope('20179506',self.words),
                   'active':identifier, 'cursor':None})
        write_json(self.root/'output_v8/runs'/identifier/'run.json', {'mail':{'status':'unknown'}})
        with self.assertRaises(V8Error) as caught:
            self.load()
        self.assertEqual(caught.exception.code, 'V8_PENDING_MAIL')

    def test_scope_or_non_korean_cursor_is_rejected(self):
        state = self.load()
        state['keywords'] = ['different']
        write_json(self.path, state)
        with self.assertRaises(V8Error):
            self.load()
        state['keywords'] = sorted(self.words)
        state['cafes']['iroid']['cursor'] = '2026-09-22T00:00:00+00:00'
        write_json(self.path, state)
        with self.assertRaises(V8Error) as caught:
            self.load()
        self.assertEqual(caught.exception.code, 'STATE_TIME_ERROR')

    def test_commit_does_not_move_cursor_backwards(self):
        state = self.load()
        state['active'] = 'fixture'
        state['cafes']['iroid']['cursor'] = '2026-09-23T09:00:00+09:00'
        record = {'id':'fixture','cafes':[{'slug':'iroid','club_id':'20179506',
                  'status':'completed','end':'2026-09-22T09:00:00+09:00'}]}
        commit_state(self.path, state, record)
        self.assertEqual(read_json(self.path)['cafes']['iroid']['cursor'], '2026-09-23T09:00:00+09:00')

    def test_invalid_run_identifier_cannot_escape_output_directory(self):
        for identifier in ('../run', '/tmp/run', 'period_invalid'):
            with self.assertRaises(V8Error) as caught:
                get_record(self.root, identifier, self.words)
            self.assertEqual(caught.exception.code, 'INVALID_RUN')
