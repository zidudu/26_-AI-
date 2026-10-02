"""State and API checks for recurring board collection; no remote requests."""
import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.sources import Sources, board_url, keyword_url
from app.store import Store
from app.jobs import Jobs
from app.ai import Vision
from app.search import Search


def picture(size):
    output = io.BytesIO()
    Image.new('RGB', size, (43, 79, 124)).save(output, 'PNG')
    return output.getvalue()


class RecurringStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.board = self.store.create_collection('보드')
        self.sources = Sources(self.store)
        self.source = self.sources.create(name='저장 보드',target='https://kr.pinterest.com/person/board/?x=1',
            collection_id=self.board['id'],interval_minutes=60,scan_limit=100,download_limit=10,enabled=False)
        self.url = 'https://www.pinterest.com/pin/123456789/'

    def tearDown(self):
        self.temp.cleanup()

    def test_board_url_requires_a_board(self):
        self.assertEqual(board_url('https://kr.pinterest.com/person/board/?x=1'),
                         'https://www.pinterest.com/person/board/')
        for url in ('https://www.pinterest.com/person/', 'https://www.pinterest.com/pin/123/',
                    'https://evil.example/person/board/'):
            with self.assertRaises(ValueError):
                board_url(url)

    def test_keyword_source_and_existing_image_origin(self):
        keyword, url = keyword_url('  캐릭터   디자인  ')
        self.assertEqual(keyword, '캐릭터 디자인')
        self.assertIn('q=%EC%BA%90', url)
        with self.assertRaises(ValueError): keyword_url('https://example.com/search')
        image,_ = self.store.add_image(picture((80,90)),{'pin_url':self.url,'source':'pinterest'})
        source = self.sources.create(name='',target=keyword,kind='keyword',
            collection_id=self.board['id'],interval_minutes=30,scan_limit=500,
            download_limit=100,run_minutes=40,enabled=False)
        self.assertEqual(source['kind'],'keyword')
        self.assertEqual(source['query_text'],keyword)
        self.assertEqual(source['name'],keyword)
        self.sources.seen(source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        pin=self.store.get(image['id'])
        self.assertIn(keyword,pin['crawl_keywords'])
        self.assertTrue(any(item['keyword']==keyword for item in pin['sightings']))

    def test_backlog_runs_earlier_than_regular_interval(self):
        self.sources.update(self.source['id'],enabled=True)
        self.sources.seen(self.source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        self.sources.mark_run(self.source['id'],'completed')
        from datetime import datetime, timezone
        due=datetime.fromisoformat(self.sources.get(self.source['id'])['next_run_at'])
        remaining=(due-datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(13*60 < remaining < 16*60, remaining)

    def test_retry_waiting_uses_backlog_interval(self):
        self.sources.update(self.source['id'],enabled=True)
        self.sources.seen(self.source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        self.sources.detail_failed(self.source['id'],self.url,'일시 오류')
        self.sources.mark_run(self.source['id'],'completed_with_errors')
        from datetime import datetime, timezone
        due=datetime.fromisoformat(self.sources.get(self.source['id'])['next_run_at'])
        remaining=(due-datetime.now(timezone.utc)).total_seconds()
        self.assertTrue(13*60 < remaining < 16*60, remaining)
        self.assertEqual(self.sources.list()[0]['retry_waiting'],1)

    def test_video_poster_is_tracked_separately(self):
        self.sources.seen(self.source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        detail={'title':'동영상 핀','description':'','pinner':'','source_url':'',
                'image_url':'https://i.pinimg.com/736x/poster.jpg','media_kind':'video_poster'}
        self.sources.record_detail(self.source['id'],self.url,detail)
        self.assertEqual(self.sources.pending(self.source['id'],1)[0]['media_kind'],'video_poster')
        self.assertEqual(self.sources.pins(self.source['id'])[0]['media_kind'],'video_poster')

    def test_discover_detail_revision_download_and_repeat(self):
        item = {'pin_url':self.url,'image_url':'https://i.pinimg.com/736x/test.jpg','title':'목록 제목'}
        self.assertTrue(self.sources.seen(self.source,item))
        self.assertEqual(len(self.sources.detail_candidates(self.source['id'],10)),1)
        self.assertEqual(self.sources.pending(self.source['id'],10),[])
        detail = {'title':'상세 제목','description':'설명','pinner':'게시자',
                  'source_url':'https://example.com/work','image_url':'https://i.pinimg.com/1200x/test.jpg'}
        self.assertTrue(self.sources.record_detail(self.source['id'],self.url,detail))
        self.assertFalse(self.sources.record_detail(self.source['id'],self.url,detail))
        with self.store.db() as db:
            count = db.execute('SELECT COUNT(*) FROM tracked_pin_revisions').fetchone()[0]
        self.assertEqual(count,1)
        self.assertEqual(len(self.sources.pending(self.source['id'],10)),1)
        image,fresh = self.store.add_image(picture((80,90)),{'pin_url':self.url,
            'image_url':detail['image_url'],'collection_id':self.board['id'],'source':'pinterest'})
        self.assertTrue(fresh)
        self.sources.succeeded(self.source['id'],self.url,image['id'])
        self.assertFalse(self.sources.seen(self.source,item))
        self.assertEqual(self.sources.pending(self.source['id'],10),[])
        self.assertEqual(self.sources.list()[0]['saved'],1)
        self.assertEqual(len(self.store.candidates(collection=self.board['id'])),1)
        self.assertTrue(self.sources.record_detail(self.source['id'],self.url,{**detail,'description':'수정된 설명'}))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tracked_pin_revisions').fetchone()[0],2)

    def test_existing_pin_prefers_larger_image(self):
        self.store.add_image(picture((32,32)),{'pin_url':self.url,'source':'pinterest'})
        bigger,_ = self.store.add_image(picture((100,120)),{'pin_url':self.url,'source':'pinterest'})
        self.sources.seen(self.source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        rows = self.sources.pins(self.source['id'])
        self.assertEqual(rows[0]['image_id'],bigger['id'])
        self.assertEqual(self.sources.list()[0]['saved'],1)

    def test_failures_backoff_and_recovery(self):
        self.sources.seen(self.source,{'pin_url':self.url,'image_url':'https://i.pinimg.com/a.jpg','title':'핀'})
        for _ in range(4):
            self.sources.detail_failed(self.source['id'],self.url,'일시 오류')
        self.assertEqual(self.sources.pins(self.source['id'])[0]['detail_state'],'error')
        self.assertEqual(self.sources.retry_errors(self.source['id']),1)
        self.assertEqual(self.sources.pins(self.source['id'])[0]['detail_state'],'pending')

    def test_restart_recovers_queued_source(self):
        self.sources.mark_queued(self.source['id'])
        Sources(self.store)
        self.assertEqual(self.sources.get(self.source['id'])['last_status'],'interrupted')

    def test_scheduler_runs_due_board_once(self):
        self.sources.update(self.source['id'],enabled=True)
        jobs = Jobs(self.store,Vision(self.store),Search(self.store))
        calls = []
        try:
            with patch.object(jobs.collector,'sync',side_effect=lambda ctx,payload,sources:calls.append(payload['source_id'])):
                jobs.start()
                for _ in range(30):
                    if calls and jobs.sources.get(self.source['id'])['last_status']=='completed':
                        break
                    time.sleep(.1)
            self.assertEqual(calls,[self.source['id']])
            self.assertEqual(jobs.sources.get(self.source['id'])['last_status'],'completed')
        finally:
            jobs.close()


class RecurringApiTests(unittest.TestCase):
    def test_existing_source_table_gets_keyword_and_runtime_columns(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp))
            with store.db() as db:
                db.execute('''CREATE TABLE tracked_sources(
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,target_url TEXT NOT NULL UNIQUE,
                    collection_id TEXT,enabled INTEGER NOT NULL,interval_minutes INTEGER NOT NULL,
                    scan_limit INTEGER NOT NULL,download_limit INTEGER NOT NULL,
                    next_run_at TEXT,last_run_at TEXT,last_status TEXT,last_message TEXT,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
                db.execute('''CREATE TABLE tracked_pins(
                    source_id TEXT NOT NULL,pin_url TEXT NOT NULL,title TEXT NOT NULL,image_url TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',pinner TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',image_id TEXT,state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,last_error TEXT,
                    detail_state TEXT NOT NULL DEFAULT 'pending',detail_attempts INTEGER NOT NULL DEFAULT 0,
                    detail_next_retry_at TEXT,detail_checked_at TEXT,detail_hash TEXT,
                    first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(source_id,pin_url))''')
            Sources(store)
            with store.db() as db:
                columns={row['name'] for row in db.execute('PRAGMA table_info(tracked_sources)')}
                pin_columns={row['name'] for row in db.execute('PRAGMA table_info(tracked_pins)')}
            self.assertTrue({'kind','query_text','run_minutes'} <= columns)
            self.assertIn('media_kind',pin_columns)

    def test_registration_requires_permission_and_exposes_status(self):
        with tempfile.TemporaryDirectory() as temp:
            app = create_app(Path(temp),seed=False)
            with TestClient(app,base_url='http://127.0.0.1') as client:
                csrf = client.get('/api/bootstrap').json()['csrf']
                headers = {'X-PinArchive-CSRF':csrf}
                board = client.post('/api/collections',headers=headers,json={'name':'로컬 보드'}).json()
                body = {'name':'원격 보드','target':'https://www.pinterest.com/person/board/',
                        'collection_id':board['id'],'enabled':False}
                self.assertEqual(client.post('/api/sources',headers=headers,json=body).status_code,400)
                created = client.post('/api/sources',headers=headers,
                    json={**body,'permission_confirmed':True})
                self.assertEqual(created.status_code,200)
                source_id = created.json()['id']
                self.assertEqual(client.get('/api/sources').json()['items'][0]['id'],source_id)
                self.assertEqual(client.get('/api/sources/'+source_id+'/pins').json()['items'],[])
                self.assertEqual(client.patch('/api/sources/'+source_id,headers=headers,
                    json={'interval_minutes':120}).json()['interval_minutes'],120)
                search = client.post('/api/sources',headers=headers,
                    json={'kind':'keyword','target':'그림 구도','collection_id':board['id'],
                          'interval_minutes':15,'download_limit':100,'run_minutes':40,
                          'enabled':False,'permission_confirmed':True})
                self.assertEqual(search.status_code,200,search.text)
                self.assertEqual(search.json()['kind'],'keyword')
                self.assertEqual(search.json()['query_text'],'그림 구도')
                self.assertEqual(search.json()['name'],'그림 구도')
                self.assertEqual(client.delete('/api/sources/'+source_id,headers=headers).status_code,200)


if __name__ == '__main__':
    unittest.main()
