import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v10.common import digest,write_json
from v10.database import Database
from v10.migrate import import_project


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'legacy';self.root.mkdir()
        self.db=Database(Path(self.tmp.name)/'v10')
        self.rid='period_20260929_100000_000001_12345678'
        self.run=self.root/'output_v9/period'/self.rid;self.run.mkdir(parents=True)
        self.source=self.run/'cafes/bestcm/collect_123/source.json'
        s=dict(cafe_id='111',article_id='7',title='후방',body='원문',body_raw='원문',written_at='2026-09-28T10:00:00+09:00',collected_at='2026-09-29T10:00:00+09:00',url='https://cafe.naver.com/bestcm/7')
        write_json(self.source,s)
        # A relocated Windows project path is a supported source locator.
        old='C:\\User\\naver_cafe\\'+str(self.source.relative_to(self.root)).replace('/','\\')
        item=dict(cafe_id='111',id='7',title='후방',url=s['url'],written_at=s['written_at'],collected_at=s['collected_at'],refreshed_source_file=old,source_artifact_sha256=digest(s),matched_keywords=['후방'])
        self.export=self.run/'export_123/summary.json';write_json(self.export,dict(items=[item],slides=0))
        record=dict(id=self.rid,mode='period',status='completed',created_at='2026-09-29T10:00:00+09:00',updated_at='2026-09-29T10:05:00+09:00',keywords=['후방'],exports=['export_123'],cafes=[dict(code='GN7',name='그랜저',slug='bestcm',club_id='111',start='2026-09-28T00:00:00+09:00',end='2026-09-29T00:00:00+09:00',collection='cafes/bestcm/collect_123',count=1)],mail={'status':'disabled'})
        write_json(self.run/'run.json',record)
    def tearDown(self):self.tmp.cleanup()

    def test_valid_moved_sources_import_once_and_keep_original_bytes(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        first=import_project(self.db,self.root);second=import_project(self.db,self.root)
        self.assertEqual(first['counts']['posts'],1);self.assertEqual(first['results'][0]['status'],'imported')
        self.assertEqual(second['results'][0]['status'],'skipped');self.assertEqual(self.db.counts()['runs'],1)
        self.assertEqual(self.db.stats()[0]['raw'],'원문')
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.db.cursors(),{})

    def test_hash_changed_source_is_excluded_not_promoted_to_verified(self):
        s=json.loads(self.source.read_text(encoding='utf-8'));s['body_raw']='익명화 변경';write_json(self.source,s)
        result=import_project(self.db,self.root)
        self.assertEqual(result['counts']['posts'],0);self.assertEqual(result['results'][0]['status'],'partial')
        self.assertIn('해시',result['results'][0]['missing'][0])

    def test_missing_source_is_explicit_and_source_run_not_reusable(self):
        self.source.unlink();result=import_project(self.db,self.root)
        run=self.db.run(result['results'][0]['runId'])
        self.assertFalse(run['hasRaw']);self.assertFalse(run['hasAnalysis']);self.assertEqual(run['articles'],[])

    def test_later_edit_creates_new_immutable_import_snapshot(self):
        first=import_project(self.db,self.root)['results'][0]['runId']
        report=json.loads(self.export.read_text(encoding='utf-8'));report['note']='뒤에 추가된 기록';write_json(self.export,report)
        second=import_project(self.db,self.root)['results'][0]['runId']
        self.assertNotEqual(first,second);self.assertEqual(self.db.counts()['posts'],1);self.assertEqual(self.db.counts()['runs'],2)


if __name__=='__main__':unittest.main()
