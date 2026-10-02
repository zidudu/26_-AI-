"""Offline regression tests. AI, DNS, downloads and encoders are mocked explicitly."""
import base64
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,MagicMock
import zipfile
import httpx
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient
from app.store import Store,MAX_IMAGE_BYTES,now,tags,link
from app.main import create_app
from app.ai import Vision,AIError
from app.search import Search,SemanticUnavailable,INDEX_MODEL
from app.network import validate_public_url,download_image,DownloadError
from app.collector import Collector,target_url
from app.jobs import Jobs
from mcp_bridge import Bridge,base_url,TOOLS
from app.runtime import publish_runtime,clear_runtime,discover_server,runtime_path
from run import bind_available_socket

def picture(color=(30,60,150),fmt='PNG',size=(32,48)):
    b=io.BytesIO();Image.new('RGB',size,color).save(b,format=fmt);return b.getvalue()

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name));self.raw=picture()
    def tearDown(self):self.temp.cleanup()
    def add(self,**kw):return self.s.add_image(self.raw,{'title':'파란색 로비','tags':['게임 UI','blue','로비'],**kw})[0]
    def test_add_real_files_and_dimensions(self):
        p=self.add();self.assertEqual((p['width'],p['height']),(32,48));self.assertTrue((self.s.images/p['filename']).exists());self.assertTrue((self.s.thumbs/(p['id']+'.webp')).exists())
    def test_exact_duplicate_merges_keywords(self):
        p=self.add(crawl_keyword='anime UI');q,new=self.s.add_image(self.raw,{'crawl_keyword':'파란 UI','pin_url':'https://www.pinterest.com/pin/123/','source':'pinterest'})
        self.assertFalse(new);self.assertEqual(p['id'],q['id']);self.assertEqual(set(q['crawl_keywords']),{'anime UI','파란 UI'});self.assertEqual(len(q['sightings']),2)
    def test_decoded_pixel_duplicate(self):
        p=self.add();q,new=self.s.add_image(picture(fmt='BMP'));self.assertFalse(new);self.assertEqual(p['id'],q['id'])
    def test_duplicate_merges_new_tags_and_empty_source(self):
        p=self.add();q,new=self.s.add_image(self.raw,{'tags':['new'],'source_url':'https://example.com/original'})
        self.assertFalse(new);self.assertIn('new',q['tags']);self.assertEqual(q['source_url'],'https://example.com/original')
    def test_alpha_differences_not_merged(self):
        a=io.BytesIO();b=io.BytesIO();Image.new('RGBA',(32,48),(0,0,0,0)).save(a,'PNG');Image.new('RGBA',(32,48),(0,0,0,255)).save(b,'PNG')
        p,_=self.s.add_image(a.getvalue());q,new=self.s.add_image(b.getvalue());self.assertTrue(new);self.assertNotEqual(p['id'],q['id'])
    def test_animations_not_deduped_by_first_frame(self):
        start=Image.new('RGB',(32,48),'red');b=io.BytesIO();start.save(b,'GIF',save_all=True,append_images=[Image.new('RGB',(32,48),'blue')],duration=100)
        p,new=self.s.add_image(b.getvalue());q,new=self.s.add_image(picture((255,0,0),fmt='GIF'));self.assertTrue(new);self.assertIn('animated',p['technical_tags'])
    def test_invalid_and_unsupported_image(self):
        for raw in (b'not an image',b'<svg></svg>',b''):
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.s.add_image(raw)
    def test_oversized_rejected(self):
        with self.assertRaises(ValueError):self.s.add_image(b'x'*(MAX_IMAGE_BYTES+1))
    def test_small_rejected(self):
        with self.assertRaises(ValueError):self.s.add_image(picture(size=(2,3)))
    def test_korean_substring_search(self):
        p=self.add();self.assertEqual(Search(self.s).search('파란')['items'][0]['id'],p['id'])
    def test_english_keyword_search(self):
        self.add();self.assertEqual(Search(self.s).search('blue')['total'],1)
    def test_search_combines_tokens_with_or(self):
        self.add();self.assertEqual(Search(self.s).search('blue nonexistent')['total'],1)
    def test_fts_special_chars_not_sql(self):
        self.add();Search(self.s).search('" OR 1=1 -- * :(');self.assertEqual(self.s.stats()['total'],1)
    def test_punctuation_only_does_not_return_entire_library(self):
        self.add();self.assertEqual(Search(self.s).search('!!!!!')['total'],0)
    def test_search_index_updates_after_edit(self):
        p=self.add();self.s.update(p['id'],{'tags':['새태그'],'title':'새제목'});self.assertEqual(Search(self.s).search('새태그')['total'],1);self.assertEqual(Search(self.s).search('blue')['total'],0)
    def test_favorite_persistence(self):
        p=self.add();self.s.update(p['id'],{'favorite':True});self.assertEqual(len(Store(self.s.root).candidates(favorite=True)),1)
    def test_collection_membership_and_duplicate_name(self):
        p=self.add();c=self.s.create_collection('레퍼런스');self.s.membership(c['id'],[p['id']]);self.s.membership(c['id'],[p['id']]);self.assertEqual(len(self.s.candidates(collection=c['id'])),1)
        with self.assertRaises(ValueError):self.s.create_collection('레퍼런스')
    def test_collection_remove_retains_image(self):
        p=self.add();c=self.s.create_collection('보드');self.s.membership(c['id'],[p['id']]);self.s.membership(c['id'],[p['id']],False)
        self.assertEqual(self.s.get(p['id'])['collections'],[]);self.assertEqual(self.s.stats()['total'],1)
    def test_duplicate_can_join_board(self):
        p=self.add();c=self.s.create_collection('두 번째');self.s.add_image(self.raw,{'collection_id':c['id']});self.assertEqual(self.s.get(p['id'])['collections'][0]['id'],c['id'])
    def test_invalid_board_rolls_back_and_removes_files(self):
        with self.assertRaises(ValueError):self.s.add_image(self.raw,{'collection_id':'invalid'})
        self.assertEqual(self.s.stats()['total'],0);self.assertEqual(list(self.s.images.iterdir()),[])
    def test_delete_cleans_files_and_search(self):
        p=self.add();self.s.delete(p['id']);self.assertFalse((self.s.images/p['filename']).exists());self.assertEqual(Search(self.s).search('blue')['total'],0)
    def test_unsafe_url_is_not_exposed_as_link(self):
        p=self.add(source_url='javascript:alert(1)');self.assertEqual(p['source_url'],'')
    def test_path_filename_not_from_title(self):
        p=self.add(title='../../outside.png');self.assertNotIn('..',p['filename']);self.assertEqual(len(list(self.s.images.iterdir())),1)
    def test_manual_data_preserved_by_ai(self):
        p=self.add();self.s.save_analysis(p['id'],{'description':'AI 추정','tags':['추정태그']},'mock','mock');q=self.s.get(p['id'])
        self.assertEqual(q['title'],p['title']);self.assertEqual(q['tags'],p['tags']);self.assertTrue(q['ai_result']['needs_review'])
    def test_hybrid_fallback_explicit(self):
        self.add();r=Search(self.s).search('blue','hybrid');self.assertEqual(r['effective_mode'],'keyword');self.assertTrue(r['warnings'])
    def test_semantic_no_index_is_error(self):
        self.add()
        with self.assertRaises(SemanticUnavailable):Search(self.s).search('blue','semantic')
    def test_vector_ranking_and_rrf_with_mock_encoder(self):
        a=self.add();b=self.s.add_image(picture((220,10,80)),{'title':'붉은색','tags':['red']})[0]
        with self.s.db() as c:
            for pid,v in [(a['id'],[0,1]),(b['id'],[1,0])]:
                v=np.array(v,dtype='<f4');c.execute('INSERT INTO vectors VALUES(?,?,?,?,?)',(pid,INDEX_MODEL,v.tobytes(),2,now()))
        search=Search(self.s);search.image_model=object();search.text_model=MagicMock();search.text_model.encode.return_value=np.array([[1,0]])
        self.assertEqual(search.search('red','semantic')['items'][0]['id'],b['id']);r=search.search('red','hybrid');self.assertEqual(r['items'][0]['id'],b['id']);self.assertFalse(r['warnings'])
    def test_dhash_label_not_semantic(self):
        p=self.add();r=Search(self.s).similar(p['id']);self.assertEqual(r['method'],'dhash');self.assertIn('의미',r['note'])
    def test_seed_once(self):
        folder=Path(self.temp.name)/'seed';(folder/'images').mkdir(parents=True);(folder/'images/x.png').write_bytes(self.raw)
        (folder/'manifest.json').write_text(json.dumps([{'image':'images/x.png','title':'샘플'}]),encoding='utf-8');self.s.seed(folder);p=self.s.candidates()[0];self.s.delete(p['id']);self.s.seed(folder);self.assertEqual(self.s.stats()['total'],0)
    def test_bounded_tags(self):
        self.assertEqual(tags(' blue,blue, 파란색 '),['blue','파란색']);self.assertLessEqual(len(tags(['a']*100)),80);self.assertEqual(tags({'x':'y'}),[])

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=create_app(Path(self.temp.name),seed=False);self.c=TestClient(self.app,base_url='http://127.0.0.1');self.c.__enter__();self.h={'X-PinArchive-CSRF':self.c.get('/api/bootstrap').json()['csrf']}
    def tearDown(self):self.c.__exit__(None,None,None);self.temp.cleanup()
    def upload(self,raw=None,**fields):return self.c.post('/api/import',headers=self.h,files=[('files',('ref.png',raw if raw is not None else picture(),'image/png'))],data=fields)
    def test_health_and_index(self):
        self.assertEqual(self.c.get('/api/health').json()['version'],'1.1.0');self.assertIn('Pin Archive',self.c.get('/').text)
    def test_csrf_rejected(self):self.assertEqual(self.c.post('/api/collections',json={'name':'test'}).status_code,403)
    def test_cross_origin_rejected(self):self.assertEqual(self.c.get('/api/bootstrap',headers={'Origin':'https://evil.example'}).status_code,403)
    def test_cross_site_rejected(self):self.assertEqual(self.c.get('/api/pins',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
    def test_rebinding_host_rejected(self):self.assertEqual(self.c.get('/api/health',headers={'Host':'evil.example'}).status_code,400)
    def test_accepted_same_origin(self):self.assertEqual(self.c.get('/api/pins',headers={'Origin':'http://127.0.0.1'}).status_code,200)
    def test_large_request_header(self):self.assertEqual(self.c.post('/api/collections',headers={**self.h,'content-length':str(101*1024*1024)},json={'name':'x'}).status_code,413)
    def test_upload_returns_real_result(self):
        r=self.upload(tags='파란색,게임 UI');self.assertEqual(r.status_code,200);self.assertEqual(r.json()['added'],1)
    def test_invalid_file_not_claimed_added(self):
        r=self.upload(b'bad');self.assertEqual(r.json()['added'],0);self.assertEqual(len(r.json()['errors']),1)
    def test_metadata_import_keeps_provenance(self):
        data=[{'title':'크롤러','tags':['robot'],'crawl_keyword':'로봇 UI','source_url':'https://example.com/original'}];r=self.upload(metadata=json.dumps(data));self.assertEqual(r.status_code,200,r.text);p=r.json()['items'][0]
        self.assertEqual(p['crawl_keywords'],['로봇 UI']);self.assertEqual(p['source_url'],'https://example.com/original');self.assertEqual(p['source'],'import')
    def test_metadata_length_error(self):self.assertEqual(self.upload(metadata='[]').status_code,400)
    def test_media_and_export(self):
        p=self.upload().json()['items'][0];self.assertEqual(self.c.get(p['image']).content,picture());r=self.c.post('/api/export',headers=self.h,json={'ids':[p['id']]});self.assertEqual(r.status_code,200)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            m=json.loads(z.read('manifest.json'));self.assertEqual(len(m['items']),1);self.assertEqual(z.read(m['items'][0]['image']),picture())
    def test_update_then_query(self):
        p=self.upload().json()['items'][0];r=self.c.patch('/api/pins/'+p['id'],headers=self.h,json={'tags':['통합시험']});self.assertEqual(r.status_code,200);self.assertEqual(self.c.get('/api/pins',params={'q':'통합'}).json()['total'],1)
    def test_delete_and_missing_media(self):
        p=self.upload().json()['items'][0];self.c.delete('/api/pins/'+p['id'],headers=self.h);self.assertEqual(self.c.get(p['image']).status_code,404)
    def test_board_delete_preserves_images(self):
        p=self.upload().json()['items'][0];board=self.c.post('/api/collections',headers=self.h,json={'name':'x'}).json();self.c.post('/api/collections/'+board['id']+'/pins',headers=self.h,json={'ids':[p['id']]});self.c.delete('/api/collections/'+board['id'],headers=self.h);self.assertEqual(self.c.get(p['image']).status_code,200)
    def test_collect_requires_permission(self):self.assertEqual(self.c.post('/api/collect',headers=self.h,json={'target':'game UI'}).status_code,400)
    def test_collect_limits_and_host(self):
        self.assertEqual(self.c.post('/api/collect',headers=self.h,json={'target':'x','limit':501,'permission_confirmed':True}).status_code,422);self.assertEqual(self.c.post('/api/collect',headers=self.h,json={'target':'https://evil.example','permission_confirmed':True}).status_code,400)
    def test_url_import_requires_permission(self):self.assertEqual(self.c.post('/api/import/urls',headers=self.h,json={'items':[{'image_url':'https://example.com/a.jpg'}]}).status_code,400)
    def test_url_import_requires_url(self):self.assertEqual(self.c.post('/api/import/urls',headers=self.h,json={'items':[{}],'permission_confirmed':True}).status_code,400)
    def test_ai_not_enabled_does_not_start(self):
        self.assertEqual(self.c.post('/api/ai/analyze',headers=self.h,json={'consent':True}).status_code,400);self.assertEqual(len(self.c.get('/api/jobs').json()['items']),0)
    def test_ai_requires_confirmation(self):self.assertEqual(self.c.post('/api/ai/analyze',headers=self.h,json={}).status_code,400)
    def test_secret_is_not_persisted_or_returned(self):
        secret='test-secret-never-real-123';r=self.c.post('/api/settings',headers=self.h,json={'provider':'openai','api_key':secret});self.assertEqual(r.status_code,200);self.assertNotIn(secret,r.text);self.assertNotIn(secret,self.c.get('/api/bootstrap').text)
        for f in Path(self.temp.name).glob('library.sqlite3*'):self.assertNotIn(secret.encode(),f.read_bytes())
    def test_request_schema_unknown_fields_rejected(self):self.assertEqual(self.c.post('/api/settings',headers=self.h,json={'unknown':'value'}).status_code,422)
    def test_security_headers(self):
        r=self.c.get('/');self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy']);self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')
    def test_empty_export_not_success(self):self.assertEqual(self.c.post('/api/export',headers=self.h,json={'ids':[]}).status_code,400)
    def test_search_limit_validated(self):self.assertEqual(self.c.get('/api/pins?limit=201').status_code,422)

class NetworkTests(unittest.TestCase):
    @staticmethod
    def dns(ip):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
    def test_private_addresses_blocked(self):
        for ip in ('127.0.0.1','10.1.1.1','169.254.169.254','192.168.1.1','::1'):
            with self.subTest(ip=ip),patch('socket.getaddrinfo',return_value=self.dns(ip)),self.assertRaises(DownloadError):validate_public_url('https://example.com/x')
    def test_mixed_dns_blocked(self):
        with patch('socket.getaddrinfo',return_value=self.dns('8.8.8.8')+self.dns('10.0.0.1')),self.assertRaises(DownloadError):validate_public_url('https://example.com/x')
    def test_public_url_accepted(self):
        with patch('socket.getaddrinfo',return_value=self.dns('8.8.8.8')):self.assertEqual(validate_public_url('https://example.com/x')[2],'8.8.8.8')
    def test_schemes_credentials_ports_blocked(self):
        for url in ('file:///etc/passwd','https://a:b@example.com/a','https://example.com:9000/a','javascript:alert(1)'):
            with self.subTest(url=url),self.assertRaises(DownloadError):validate_public_url(url)
    def test_pinterest_only_host(self):
        with self.assertRaises(DownloadError):download_image('https://example.com/a',pinterest_only=True)
    def test_redirect_revalidated(self):
        response=MagicMock();response.status=302;response.getheader.return_value='http://127.0.0.1/secret';conn=MagicMock();conn.getresponse.return_value=response
        with patch('app.network.validate_public_url',side_effect=[('example.com',443,'8.8.8.8'),DownloadError('private')]) as validate,patch('http.client.HTTPSConnection',return_value=conn):
            with self.assertRaises(DownloadError):download_image('https://example.com/x')
            self.assertEqual(validate.call_count,2)
    def test_keyword_url_encoded(self):self.assertIn('%ED',target_url('한글 UI'));self.assertIn('q=',target_url('game ui'))
    def test_pinterest_url_validation(self):
        self.assertEqual(target_url('https://www.pinterest.com/a/board/'),'https://www.pinterest.com/a/board/')
        for url in ('https://pinterest.com.evil.com/x','http://www.pinterest.com/x','https://localhost/x'):
            with self.subTest(url=url),self.assertRaises(ValueError):target_url(url)

class CollectorLoginTests(unittest.TestCase):
    def test_windows_chrome_login_uses_dedicated_profile_without_automation(self):
        with tempfile.TemporaryDirectory() as temp:
            chrome=Path(temp)/'Google'/'Chrome'/'Application'/'chrome.exe'
            chrome.parent.mkdir(parents=True)
            chrome.touch()
            store=MagicMock()
            store.root=Path(temp)/'data'
            store.settings.return_value={'browser_channel':'chrome'}
            ctx=MagicMock()
            with patch('app.collector.os') as fake_os,patch('app.collector.subprocess.Popen') as popen:
                fake_os.name='nt'
                fake_os.environ={'PROGRAMFILES':temp}
                Collector(store).login(ctx,{})
            popen.assert_called_once_with(
                [str(chrome),f'--user-data-dir={store.root / "pinterest_profile"}',
                 '--no-first-run','https://www.pinterest.com/login/'],close_fds=True)
            self.assertIn('직접 로그인',ctx.update.call_args.kwargs['message'])

class AITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name));self.v=Vision(self.s);self.p=self.s.add_image(picture(),{'title':'수동 제목'})[0]
        self.response={'title':'제안','description':'청색 사각형','category':'도형','tags':['blue','파란색'],'colors':['blue'],'layout':'중앙','uncertainty':'테스트 응답'}
    def tearDown(self):self.temp.cleanup()
    def test_openai_request_shape_and_analysis_mock(self):
        self.s.save_settings({'provider':'openai'});self.v.set_key('test-only');r=httpx.Response(200,json={'status':'completed','output':[{'content':[{'type':'output_text','text':json.dumps(self.response)}]}]})
        with patch('app.ai.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=r;self.v.analyze(self.p['id']);kw=client.return_value.__enter__.return_value.post.call_args.kwargs
            self.assertFalse(kw['json']['store']);self.assertTrue(kw['json']['text']['format']['strict']);self.assertEqual(kw['json']['input'][0]['content'][1]['type'],'input_image')
        self.assertEqual(self.s.get(self.p['id'])['title'],'수동 제목');self.assertEqual(self.s.get(self.p['id'])['ai_status'],'done')
    def test_ollama_native_image_schema_mock(self):
        self.s.save_settings({'provider':'ollama','ollama_model':'vision-test'});r=httpx.Response(200,json={'message':{'content':json.dumps(self.response)}})
        with patch('app.ai.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=r;self.v.analyze(self.p['id']);call=client.return_value.__enter__.return_value.post.call_args
            self.assertEqual(call.args[0],'http://127.0.0.1:11434/api/chat');self.assertIn('images',call.kwargs['json']['messages'][0]);self.assertFalse(call.kwargs['json']['stream'])
    def test_http_error_does_not_leak_response(self):
        with self.assertRaises(AIError) as e:self.v.check(httpx.Response(401,text='secret response body'))
        self.assertNotIn('secret response',str(e.exception))
    def test_malformed_model_output_is_error(self):
        self.s.save_settings({'provider':'ollama','ollama_model':'x'})
        with patch('app.ai.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=httpx.Response(200,json={'message':{'content':'not-json'}})
            with self.assertRaises(AIError):self.v.analyze(self.p['id'])
        self.assertNotEqual(self.s.get(self.p['id'])['ai_status'],'done')
    def test_incomplete_output_is_error(self):
        self.s.save_settings({'provider':'openai'});self.v.set_key('test-only')
        with patch('app.ai.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=httpx.Response(200,json={'status':'incomplete'})
            with self.assertRaises(AIError):self.v.analyze(self.p['id'])
    def test_rerank_cannot_inject_unknown_id(self):
        with patch.object(self.v,'call_json',return_value=({'ordered_ids':['evil',self.p['id'],self.p['id']],'reason':'test'},'mock','mock')):
            self.assertEqual(self.v.rerank('blue',[self.p['id']])['ordered_ids'],[self.p['id']])

class MCPTests(unittest.TestCase):
    def setUp(self):self.b=Bridge('http://127.0.0.1:8765')
    def request(self,method,params=None):return self.b.dispatch({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}})
    def test_handshake_and_tools(self):self.assertEqual(self.request('initialize',{'protocolVersion':'2025-06-18'})['result']['protocolVersion'],'2025-06-18');self.assertEqual(len(self.request('tools/list')['result']['tools']),6)
    def test_only_readonly_tools(self):self.assertTrue(all(t['annotations']['readOnlyHint'] for t in TOOLS));self.assertFalse(any(t['annotations']['destructiveHint'] for t in TOOLS))
    def test_invalid_id_and_arguments(self):
        for args in ({'pin_id':'../../x'},{'pin_id':'a'*32,'extra':1}):self.assertEqual(self.request('tools/call',{'name':'get_image','arguments':args})['error']['code'],-32602)
    def test_missing_query_or_invalid_limit(self):
        for args in ({},{'query':'x','limit':31},{'query':'x','limit':True}):self.assertEqual(self.request('tools/call',{'name':'search_images','arguments':args})['error']['code'],-32602)
    def test_external_server_rejected(self):
        for url in ('https://example.com:8765','http://user:pass@localhost:8765','http://localhost:8765/api'):
            with self.assertRaises(ValueError):base_url(url)
    def test_image_contains_actual_base64(self):
        raw=picture(fmt='WEBP')
        with patch.object(self.b,'get',side_effect=[{'id':'a'*32,'width':32},raw]):
            r=self.b.tool('get_image',{'pin_id':'a'*32});self.assertEqual(base64.b64decode(r['content'][1]['data']),raw);self.assertEqual(r['content'][1]['mimeType'],'image/webp')
    def test_show_images_returns_multiple_ordered_pixels(self):
        first=picture(fmt='WEBP');second=picture(fmt='WEBP')
        items=[{'id':'a'*32,'title':'first'},{'id':'b'*32,'title':'second'}]
        with patch.object(self.b,'get',side_effect=[{'total':2,'items':items},first,second]) as get:
            r=self.b.tool('show_images',{'query':'','limit':2})
        self.assertFalse(r['isError'])
        self.assertEqual([x['type'] for x in r['content']],['text','text','image','text','image'])
        self.assertEqual(base64.b64decode(r['content'][2]['data']),first)
        self.assertEqual(base64.b64decode(r['content'][4]['data']),second)
        self.assertEqual([x['id'] for x in r['structuredContent']['items']],['a'*32,'b'*32])
        self.assertIn('limit=2',get.call_args_list[0].args[0])
    def test_gallery_resource_is_read_only_html(self):
        tools=self.request('tools/list')['result']['tools']
        show=next(t for t in tools if t['name']=='show_images')
        uri=show['_meta']['ui']['resourceUri']
        listed=self.request('resources/list')['result']['resources']
        self.assertEqual(listed[0]['uri'],uri)
        page=self.request('resources/read',{'uri':uri})['result']['contents'][0]
        self.assertEqual(page['mimeType'],'text/html;profile=mcp-app')
        self.assertIn('ui/notifications/tool-result',page['text'])
        self.assertEqual(self.request('resources/read',{'uri':'ui://other'})['error']['code'],-32602)
    def test_show_images_limit_is_bounded(self):
        self.assertEqual(self.request('tools/call',{'name':'show_images','arguments':{'query':'','limit':7}})['error']['code'],-32602)
    def test_execution_errors_is_error_not_success(self):
        with patch.object(self.b,'get',side_effect=RuntimeError('offline')):self.assertTrue(self.request('tools/call',{'name':'library_status'})['result']['isError'])
    def test_unknown_method_and_notification(self):self.assertEqual(self.request('unknown')['error']['code'],-32601);self.assertIsNone(self.b.dispatch({'jsonrpc':'2.0','method':'notifications/initialized'}))
    def test_invalid_message(self):self.assertEqual(self.b.dispatch([])['error']['code'],-32600)

class RuntimeTests(unittest.TestCase):
    def test_busy_port_is_skipped_while_socket_is_held(self):
        occupied=socket.socket();occupied.bind(('127.0.0.1',0));occupied.listen(1)
        try:
            sock,port=bind_available_socket(occupied.getsockname()[1],max_tries=5)
            try:
                self.assertGreater(port,occupied.getsockname()[1])
                with self.assertRaises(OSError):
                    other=socket.socket()
                    try: other.bind(('127.0.0.1',port))
                    finally: other.close()
            finally: sock.close()
        finally: occupied.close()

    def test_runtime_discovery_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);instance='a'*32
            publish_runtime(root,8766,instance)
            response=MagicMock()
            response.__enter__.return_value.read.return_value=json.dumps({'app':'pinarchive','instance_id':instance}).encode()
            with patch('app.runtime.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=response
                self.assertEqual(discover_server(root),'http://127.0.0.1:8766')
                opener.return_value.open.assert_called_once()
            clear_runtime(root,'b'*32)
            self.assertTrue(runtime_path(root).exists())
            clear_runtime(root,instance)
            self.assertFalse(runtime_path(root).exists())

    def test_stale_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            runtime_path(root).write_text('{broken',encoding='utf-8')
            with self.assertRaises(RuntimeError): discover_server(root)
            publish_runtime(root,8766,'a'*32)
            response=MagicMock()
            response.__enter__.return_value.read.return_value=b'{"app":"pinarchive","instance_id":"different"}'
            with patch('app.runtime.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value=response
                with self.assertRaises(RuntimeError): discover_server(root)

class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name));self.v=Vision(self.s);self.j=Jobs(self.s,self.v,Search(self.s))
    def tearDown(self):self.j.close();self.temp.cleanup()
    def wait(self,jid):
        for _ in range(300):
            j=self.s.job(jid)
            if j['status'] not in ('queued','running'):return j
            time.sleep(.01)
        self.fail('Worker did not finish')
    def test_url_job_real_storage_mock_transport(self):
        with patch('app.jobs.download_image',return_value=picture()):j=self.j.submit('url_import',{'items':[{'image_url':'https://example.com/a.png','tags':['mocktransport']}]});done=self.wait(j['id'])
        self.assertEqual(done['status'],'completed');self.assertEqual(done['added'],1);self.assertEqual(self.s.stats()['total'],1)
    def test_partial_failures_are_not_silent(self):
        with patch('app.jobs.download_image',side_effect=[picture(),ValueError('failed')]):j=self.j.submit('url_import',{'items':[{'image_url':'https://example.com/a'},{'image_url':'https://example.com/b'}]});done=self.wait(j['id'])
        self.assertEqual(done['status'],'completed_with_errors');self.assertEqual(done['errors'],1)
    def test_all_failures_status_is_failed(self):
        with patch('app.jobs.download_image',side_effect=ValueError('failed')):j=self.j.submit('url_import',{'items':[{'image_url':'https://example.com/a'}]});done=self.wait(j['id'])
        self.assertEqual(done['status'],'failed');self.assertEqual(done['added'],0)
    def test_secret_redaction(self):self.v.set_key('secret-test');self.assertNotIn('secret-test',self.j.safe_error(ValueError('secret-test issue')))
    def test_cancel_queued_job(self):
        release=threading.Event();entered=threading.Event()
        def block(*args):entered.set();release.wait(3);return picture()
        with patch('app.jobs.download_image',side_effect=block):
            first=self.j.submit('url_import',{'items':[{'image_url':'https://example.com/a'}]});self.assertTrue(entered.wait(1));second=self.j.submit('url_import',{'items':[{'image_url':'https://example.com/b'}]});self.assertTrue(self.j.cancel(second['id']));release.set();self.wait(first['id']);self.assertEqual(self.wait(second['id'])['status'],'cancelled')
    def test_restart_marks_stale_job_interrupted(self):
        with self.s.db() as c:c.execute('INSERT INTO jobs(id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?)',('stale','collect','running','{}',now(),now()))
        other=Jobs(self.s,self.v,Search(self.s))
        try:self.assertEqual(self.s.job('stale')['status'],'interrupted')
        finally:other.close()

if __name__=='__main__':unittest.main()
