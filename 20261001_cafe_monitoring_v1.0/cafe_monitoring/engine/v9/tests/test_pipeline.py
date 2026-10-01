"""Exercise the real Runner with disposable state and a simulated backend."""
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from v8.configuration import DEFAULT, V8Error, parse_time, read_json, write_json
from v9.configuration import CATALOG, PERFORMANCE_DEFAULTS
from v9.pipeline import Runner, unique_items

NOW = parse_time('2026-09-23 10:00')


def config():
    cfg = deepcopy(DEFAULT)
    cfg.update(version='9.0.0', inherit_v8_cursor=False,
               collection={'concurrency': 1}, performance=deepcopy(PERFORMANCE_DEFAULTS))
    cfg['schedule']['initial_start'] = '2026-09-21 09:00'
    cfg['mail']['to'] = ['test@example.invalid']
    cfg['mail']['cc'] = []
    cfg['mail']['send_partial'] = True
    cfg['cafes'] = [dict(code=c, name=n, slug=s, club_id=i, id_source='fixture',
                         enabled=s in ('iroid', 'ite')) for c, n, s, i in CATALOG]
    return cfg


class FakeBackend:
    words = ['SCC', '경고등']

    def __init__(self, root):
        self.legacy_out = root / 'legacy_output'
        self.calls = []
        self.fail_cafes = set()
        self.empty_cafes = set()
        self.review_cafes = set()
        self.block_cafes = set()
        self.fail_export = False
        self.interrupt_after_export = False
        self.invalid_export = None

    def check(self):
        self.calls.append('check')

    @contextmanager
    def browser(self):
        yield self

    def identify(self, cafe):
        return cafe['club_id'], {'fixture': True}

    def collect(self, cafe, folder, start, end):
        self.calls.append('collect:' + cafe['slug'])
        if cafe['slug'] in self.block_cafes:
            raise V8Error('REQUEST_BLOCKED', 'Simulated site block')
        if cafe['slug'] in self.fail_cafes:
            raise V8Error('PAGE_NOT_READY', 'Simulated incomplete collection')
        items = [] if cafe['slug'] in self.empty_cafes else [{
            'cafe_id': cafe['club_id'], 'id': '101', 'cafe_slug': cafe['slug'],
            'url': 'https://example.invalid/' + cafe['club_id'] + '/101',
            'written_at': start.isoformat(),
            'selection_window': {'start': start.isoformat(), 'end': end.isoformat()},
            'source_artifact_sha256': 'fixture-sha-' + cafe['club_id'],
            'matched_keywords': ['SCC'], 'analysis_issues': [],
        }]
        write_json(folder / 'collection.json', {'items': items, 'complete': True,
                   'start': start.isoformat(), 'end': end.isoformat()})

    def load_collection(self, cafe, folder, start, end):
        saved = read_json(folder / 'collection.json')
        if not saved['complete'] or (saved['start'], saved['end']) != (start.isoformat(), end.isoformat()):
            raise V8Error('COLLECTION_MISMATCH', 'Fixture interval mismatch')
        return saved['items']

    def export(self, items, folder, cafes):
        self.calls.append('export')
        if self.fail_export:
            raise V8Error('PPT_FAILED', 'Simulated Office failure')
        copied = deepcopy(items)
        for a in copied:
            if a['cafe_slug'] in self.review_cafes:
                a['analysis_issues'] = [{'code': 'NEEDS_REVIEW'}]
        partial = any(a['analysis_issues'] for a in copied)
        report = dict(items=copied, collection_complete=True, selected_articles=len(items),
                      status=('partial' if partial else 'completed') if items else 'completed_empty',
                      reopened_structure_verified=bool(items), slides=len(items))
        if items:
            path = folder / 'monitoring.pptx'
            path.write_bytes(b'FAKE artifact; not a real PowerPoint validation')
            report['pptx'] = str(path)
        if self.invalid_export == 'unverified':
            report['reopened_structure_verified'] = False
        elif self.invalid_export == 'wrong_source':
            copied[0]['source_artifact_sha256'] = 'changed'
        elif self.invalid_export == 'incomplete':
            report['collection_complete'] = False
        elif self.invalid_export == 'missing':
            copied.pop()
        write_json(folder / 'summary.json', report)
        if self.interrupt_after_export:
            raise KeyboardInterrupt('Fixture: saved before interrupted return')
        return report, 2 if partial else 0


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='v9_한글 공백_')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = config()
        self.backend = FakeBackend(self.root)
        self.sent = []
        self.fail_mail = False
        self.runner = Runner(self.root, self.cfg, self.backend, self.sender)

    def sender(self, **payload):
        # This is the only sender used in this suite; no connector/Outlook import.
        self.assertEqual(read_json(self.runner.last_folder / 'run.json')['mail']['status'], 'sending')
        self.sent.append(payload)
        if self.fail_mail:
            raise TimeoutError('Fixture: delivery outcome unknown')
        return {'ok': True, 'action': 'sent'}

    def execute(self, **kwargs):
        with redirect_stdout(StringIO()):
            return self.runner.execute(now=kwargs.pop('now', NOW), **kwargs)[0]

    def record(self):
        return read_json(self.runner.last_folder / 'run.json')

    def state(self):
        return read_json(self.runner.state_path)

    def test_scheduled_completed_is_not_recollected_or_resent(self):
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.backend.calls.count('export'), 1)
        self.assertEqual(len(self.sent), 1)
        for slug in ('iroid', 'ite'):
            self.assertEqual(self.state()['cafes'][slug]['cursor'], '2026-09-23T09:00:00+09:00')

    def test_period_run_preserves_scheduled_state_and_never_resends(self):
        self.execute()
        before = self.runner.state_path.read_bytes()
        self.assertEqual(self.execute(start='2026-09-21 09:00', end='2026-09-23 09:00', send_mail=True), 0)
        identifier = self.record()['id']
        self.assertEqual(self.execute(identifier=identifier), 0)
        self.assertEqual(self.runner.state_path.read_bytes(), before)
        self.assertEqual(len(self.sent), 2)

    def test_first_period_run_does_not_create_schedule_state(self):
        self.assertEqual(self.execute(start='2026-09-21 09:00', end='2026-09-23 09:00'), 0)
        self.assertFalse(self.runner.state_path.exists())
        self.assertEqual(self.sent, [])

    def test_unknown_mail_never_retries_automatically(self):
        self.fail_mail = True
        self.assertEqual(self.execute(), 3)
        self.fail_mail = False
        self.assertEqual(self.execute(retry=True), 3)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNone(self.state()['cafes']['iroid']['cursor'])

    def test_direct_delivery_boundary_also_blocks_unknown_outcome(self):
        self.fail_mail = True
        self.execute()
        self.fail_mail = False
        record = self.record()
        with redirect_stdout(StringIO()):
            self.runner.send(self.runner.last_folder, record)
        self.assertEqual(record['status'], 'mail_unknown')
        self.assertEqual(len(self.sent), 1)

    def test_manual_sent_resolution_reuses_artifact_without_second_send(self):
        self.fail_mail = True
        self.execute()
        self.runner.resolve_mail(self.record()['id'], 'sent')
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.backend.calls.count('export'), 1)

    def test_manual_not_sent_resolution_reuses_artifact(self):
        self.fail_mail = True
        self.execute()
        self.runner.resolve_mail(self.record()['id'], 'not-sent')
        self.fail_mail = False
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.backend.calls.count('export'), 1)

    def test_one_failed_cafe_keeps_cursor_but_successful_cafe_advances(self):
        self.backend.fail_cafes.add('ite')
        self.assertEqual(self.execute(), 2)
        saved = self.state()['cafes']
        self.assertIsNone(saved['ite']['cursor'])
        self.assertEqual(saved['ite']['failed_end'], '2026-09-23T09:00:00+09:00')
        self.assertEqual(saved['iroid']['cursor'], '2026-09-23T09:00:00+09:00')

    def test_review_partial_can_advance_collection_cursor(self):
        self.backend.review_cafes.add('ite')
        self.assertEqual(self.execute(), 2)
        self.assertEqual(self.state()['cafes']['ite']['cursor'], '2026-09-23T09:00:00+09:00')

    def test_all_empty_has_no_mail_and_advances(self):
        self.backend.empty_cafes.update(('iroid', 'ite'))
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.record()['status'], 'completed_empty')
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.state()['active'])

    def test_incomplete_export_cannot_be_mailed(self):
        self.backend.invalid_export = 'incomplete'
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.state()['cafes']['iroid']['cursor'])

    def test_unverified_ppt_cannot_be_mailed(self):
        self.backend.invalid_export = 'unverified'
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.sent, [])

    def test_changed_source_cannot_be_mailed(self):
        self.backend.invalid_export = 'wrong_source'
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.record()['error']['code'], 'EXPORT_SOURCE_MISMATCH')
        self.assertEqual(self.sent, [])

    def test_missing_article_marks_only_affected_cafe_failed(self):
        self.backend.invalid_export = 'missing'
        self.assertEqual(self.execute(), 2)
        self.assertIsNone(self.state()['cafes']['ite']['cursor'])
        self.assertIsNotNone(self.state()['cafes']['iroid']['cursor'])

    def test_export_retry_reuses_collection_and_original_interval(self):
        self.backend.fail_export = True
        self.assertEqual(self.execute(), 1)
        self.assertEqual(self.execute(), 1)
        original = self.record()['cafes'][0]['end']
        self.backend.fail_export = False
        self.assertEqual(self.execute(now=parse_time('2026-09-25 12:00'), retry=True), 0)
        self.assertEqual(self.backend.calls.count('collect:iroid'), 1)
        self.assertEqual(self.record()['cafes'][0]['end'], original)

    def test_completed_export_is_recovered_after_interrupt(self):
        self.backend.interrupt_after_export = True
        self.assertEqual(self.execute(), 130)
        self.backend.interrupt_after_export = False
        self.assertEqual(self.execute(retry=True), 0)
        self.assertEqual(self.backend.calls.count('export'), 1)

    def test_held_partial_reuses_exact_artifact(self):
        self.backend.review_cafes.add('iroid')
        self.runner.cfg['mail']['send_partial'] = False
        self.assertEqual(self.execute(), 2)
        self.assertEqual(self.sent, [])
        self.runner.cfg['mail']['send_partial'] = True
        self.assertEqual(self.execute(retry=True), 2)
        expected = self.runner.last_folder / self.record()['artifact']['ppt']
        self.assertEqual(self.sent[0]['attachments'], [str(expected)])
        self.assertEqual(self.backend.calls.count('export'), 1)

    def test_tampered_ppt_is_not_sent_after_hold(self):
        self.backend.review_cafes.add('iroid')
        self.runner.cfg['mail']['send_partial'] = False
        self.execute()
        (self.runner.last_folder / self.record()['artifact']['ppt']).write_bytes(b'changed')
        self.runner.cfg['mail']['send_partial'] = True
        self.assertEqual(self.execute(retry=True), 1)
        self.assertEqual(self.record()['error']['code'], 'ARTIFACT_CHANGED')
        self.assertEqual(self.sent, [])

    def test_state_write_recovery_does_not_resend(self):
        self.execute()
        record = self.record()
        state = self.state()
        state['active'] = record['id']
        for slug in ('iroid', 'ite'):
            state['cafes'][slug]['cursor'] = None
        write_json(self.runner.state_path, state)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIsNotNone(self.state()['cafes']['iroid']['cursor'])

    def test_site_block_stops_later_cafes(self):
        self.backend.block_cafes.add('iroid')
        self.execute()
        self.assertNotIn('collect:ite', self.backend.calls)
        self.assertEqual(self.record()['cafes'][1]['error']['code'], 'DEFERRED_REQUEST_BLOCKED')

    def test_composite_identity_allows_same_post_number_in_different_cafes(self):
        self.assertEqual(unique_items([{'cafe_id': '1', 'id': '9'}, {'cafe_id': '2', 'id': '9'}]), {'1:9', '2:9'})
        with self.assertRaises(V8Error):
            unique_items([{'cafe_id': '1', 'id': '9'}] * 2)


if __name__ == '__main__':
    unittest.main()
