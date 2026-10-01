from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from v754.core import V7Error, write_json
from v9.template_cache import create_or_load, sha


def write_template(path):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('ppt/slides/slide1.xml', '<slide/>')


class Deck:
    def __init__(self):
        self.count = 0
        self.fail_delete = False
        self.fail_save = False
        self.Slides = self

    @property
    def Count(self):
        return self.count

    def Item(self, number):
        return SimpleNamespace(Name='V93_INTERNAL_TEMPLATE', Delete=self.delete)

    def delete(self):
        if self.fail_delete:
            raise RuntimeError('fixture delete failure')
        self.count -= 1

    def InsertFromFile(self, *args):
        self.count += 1
        return 1

    def make_frame(self, *args):
        self.count += 1
        return self.Item(self.count)

    def SaveCopyAs(self, path, *args):
        if self.fail_save:
            raise RuntimeError('fixture save failure')
        write_template(path)


class TemplateCacheTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root/'fixture.pptx'
        self.manifest = self.root/'fixture.json'
        write_template(self.path)
        write_json(self.manifest, {'key':'fixture','sha256':sha(self.path)})
        self.report = {}
        self.deck = Deck()

    def run_cache(self, validate=None):
        with patch('v9.template_cache.cache_paths', return_value=(self.path,self.manifest,'fixture')), \
             patch('v9.template_cache.validate_slide', side_effect=validate), \
             patch('v9.template_cache.legacy.make_frame', self.deck.make_frame), redirect_stdout(StringIO()):
            return create_or_load(self.deck, {}, self.report)

    def test_valid_template_reuses_one_slide(self):
        self.run_cache()
        self.assertTrue(self.report['ppt_template_cache']['hit'])
        self.assertEqual(self.report['ppt_template_builds'], 0)
        self.assertEqual(self.deck.count, 1)

    def test_bad_checksum_reports_stage_and_rebuilds(self):
        write_json(self.manifest, {'key':'fixture','sha256':'wrong'})
        self.run_cache()
        detail = self.report['ppt_template_cache']
        self.assertEqual(detail['load_error'], 'ValueError')
        self.assertEqual(detail['load_failure']['stage'], 'verify_checksum')
        self.assertIn('checksum', detail['load_failure']['message'])
        self.assertTrue(detail['saved'])
        self.assertEqual(self.deck.count, 1)

    def test_bad_cached_slide_is_removed_before_fallback(self):
        self.run_cache([ValueError('fixture wrong metadata'), None])
        self.assertEqual(self.deck.count, 1)
        self.assertEqual(self.report['ppt_template_cache']['load_failure']['stage'], 'validate_slide')
        self.assertTrue(self.report['ppt_template_cache']['saved'])

    def test_failed_cleanup_stops_instead_of_leaving_an_extra_slide(self):
        self.deck.fail_delete = True
        with self.assertRaises(V7Error) as caught:
            self.run_cache([ValueError('fixture wrong metadata')])
        self.assertEqual(caught.exception.code, 'TEMPLATE_CLEANUP_FAILED')
        self.assertEqual(self.deck.count, 1)
        self.assertEqual(self.report['ppt_template_cache']['cleanup_failure']['stage'], 'remove_inserted_slide')

    def test_save_failure_keeps_current_frame_and_records_stage(self):
        self.manifest.unlink()
        self.deck.fail_save = True
        self.run_cache()
        detail = self.report['ppt_template_cache']
        self.assertEqual(detail['miss_reason'], 'cache_files_missing')
        self.assertEqual(detail['save_failure']['stage'], 'save_copy')
        self.assertFalse(detail['saved'])
        self.assertEqual(self.deck.count, 1)
