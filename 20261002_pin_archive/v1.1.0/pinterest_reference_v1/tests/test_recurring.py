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
from app.sources import Sources, board_url
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
                self.assertEqual(client.delete('/api/sources/'+source_id,headers=headers).status_code,200)


if __name__ == '__main__':
    unittest.main()
