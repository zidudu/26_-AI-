"""COM boundary simulation; these tests do not claim actual Office rendering."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from v754.core import V7Error
from v8.configuration import KST
from v9.powerpoint import render


class FakeDeck:
    def __init__(self):
        self.Slides = SimpleNamespace(Count=0)
        self.PageSetup = SimpleNamespace()
        self.closed = False

    def SaveAs(self, path, format):
        Path(path).write_bytes(b'simulated saved presentation')

    def Close(self):
        self.closed = True


class PptLifecycleTests(unittest.TestCase):
    def render_fixture(self, folder, *, reopened_count=10):
        deck = FakeDeck()
        reopened = SimpleNamespace(Slides=SimpleNamespace(Count=reopened_count))
        presentations = SimpleNamespace(Add=Mock(return_value=deck), Open=Mock(return_value=reopened))
        app = SimpleNamespace(Presentations=presentations, Quit=Mock())
        pythoncom = SimpleNamespace(CoInitialize=Mock(), CoUninitialize=Mock())
        dynamic = SimpleNamespace(Dispatch=Mock(return_value=app))
        modules = {'pythoncom':pythoncom, 'win32com':SimpleNamespace(),
                   'win32com.client':SimpleNamespace(dynamic=dynamic)}
        report = {'dashboard_stats':{'total_posts':0}, 'dashboard_manifest':[], 'warnings':[]}
        states = []

        def append(actual_deck, cfg, actual_report, articles, targets):
            self.assertEqual(targets, {})
            actual_deck.Slides.Count = 10
            return 10

        with ExitStack() as stack:
            stack.enter_context(patch.dict('sys.modules', modules))
            stack.enter_context(patch('v9.powerpoint.ensure_windows'))
            stack.enter_context(patch('v9.powerpoint.get_dimensions'))
            stack.enter_context(patch('v9.powerpoint.prepare_ppt_images'))
            clock = stack.enter_context(patch('v9.powerpoint.datetime'))
            clock.now.return_value = datetime(2026,9,29,9,0,tzinfo=KST)
            stack.enter_context(patch('v9.dashboard_ppt.append_dashboard', side_effect=append))
            verify = stack.enter_context(patch('v9.dashboard_ppt.verify_dashboard'))
            stack.enter_context(redirect_stdout(StringIO()))
            try:
                render([], {'max_slides':100,'capture_columns':2}, folder, report,
                       lambda: states.append(report.get('stage')), dashboard_resolver=lambda articles: None)
            except V7Error as exc:
                report['_test_exception'] = exc.code
        return report, states, app, pythoncom, deck, verify

    def test_empty_dashboard_saves_dated_path_reopens_and_keeps_office_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report, states, app, com, deck, verify = self.render_fixture(folder)
            self.assertEqual(report['pptx'], str(folder/'20260929_monitoring.pptx'))
            self.assertEqual(app.Presentations.Open.call_args.args[0], report['pptx'])
            self.assertTrue(report['reopened_structure_verified'])
            self.assertEqual(report['dashboard_reopen_checked'], 10)
            self.assertEqual(report['stage'], 'COMPLETE')
            self.assertLess(states.index('SAVE_PPTX'), states.index('REOPEN_PPTX'))
            self.assertEqual([call.args[2] for call in verify.call_args_list], ['after_save','reopen'])
            self.assertTrue(deck.closed)
            app.Quit.assert_not_called()
            com.CoInitialize.assert_called_once()
            com.CoUninitialize.assert_called_once()

    def test_reopen_count_mismatch_cannot_mark_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, states, app, com, deck, verify = self.render_fixture(Path(tmp), reopened_count=9)
            self.assertEqual(report['_test_exception'], 'REOPEN_MISMATCH')
            self.assertIsNot(report.get('reopened_structure_verified'), True)
            self.assertNotEqual(report['stage'], 'COMPLETE')
            com.CoUninitialize.assert_called_once()
            app.Quit.assert_not_called()
