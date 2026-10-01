"""Loopback-only HTTP application. No external framework is required."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote,quote
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import threading
import time
from .common import ROOT,Problem,dumps
from .settings import validate


class AppServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,service):
        self.service=service;self.token=secrets.token_urlsafe(32)
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    server_version='V10'
    REJECT_DRAIN_TIMEOUT=2.0
    def log_message(self,*_):pass

    def headers_ok(self,write=False):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if host not in allowed:raise Problem('허용되지 않은 Host입니다.',403,'INVALID_HOST')
        origin=self.headers.get('Origin')
        if origin is not None and origin not in {'http://'+h for h in allowed}:raise Problem('같은 V10 화면에서 요청하세요.',403,'INVALID_ORIGIN')
        if write:
            token=self.headers.get('X-V10-Token','')
            if not hmac.compare_digest(token,self.server.token):raise Problem('화면을 새로고침한 뒤 다시 시도하세요.',403,'INVALID_TOKEN')
            if self.headers.get_content_type()!='application/json':raise Problem('JSON 요청이 필요합니다.',415)

    def respond(self,status,body,kind='application/json; charset=utf-8',extra=None):
        if not isinstance(body,bytes):body=dumps(body).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer')
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):pass

    def body(self):
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise Problem('Content-Length 오류') from None
        if size<0 or size>8*1024*1024:raise Problem('요청 크기가 너무 큽니다.',413)
        try:
            value=json.loads(self.rfile.read(size),parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError,UnicodeError):raise Problem('JSON 요청을 확인하세요.') from None
        if not isinstance(value,dict):raise Problem('JSON 객체를 요청하세요.')
        return value

    def discard_rejected_body(self):
        """Receive a bounded body before closing an already-rejected request.

        urllib/browser clients can send headers and JSON in separate packets.
        Closing while the JSON is arriving can lose the HTTP error to a TCP
        reset. Discard bytes only: never parse or execute a rejected request.
        """
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:return
        if not 0<size<=8*1024*1024 or self.headers.get('Transfer-Encoding'):return
        previous=self.connection.gettimeout()
        deadline=time.monotonic()+self.REJECT_DRAIN_TIMEOUT
        try:
            while size:
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                self.connection.settimeout(remaining)
                chunk=self.rfile.read1(min(size,65536))
                if not chunk:break
                size-=len(chunk)
        except OSError:pass
        finally:self.connection.settimeout(previous)

    def handle_error(self,exc):
        self.respond(getattr(exc,'status',500),{'error':getattr(exc,'code','INTERNAL_ERROR'),'message':str(exc) if isinstance(exc,(Problem,ValueError,OSError)) else '서버 처리 중 오류가 발생했습니다. 실행 로그를 확인하세요.'})

    def do_GET(self):
        try:
            self.headers_ok();url=urlsplit(self.path);path=unquote(url.path);q=parse_qs(url.query);s=self.server.service
            if path=='/':
                html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('__V10_TOKEN__',self.server.token)
                return self.respond(200,html.encode(),'text/html; charset=utf-8')
            if path in ('/base.js','/integration.js'):
                return self.respond(200,(ROOT/'web'/path[1:]).read_bytes(),'text/javascript; charset=utf-8')
            if path=='/api/health':return self.respond(200,{'version':'10.0.0','status':'ok'})
            if path=='/api/state':
                seq=s.db.get('sequence',0)
                if q.get('since',[''])[0]==str(seq):return self.respond(200,{'unchanged':True,'sequence':seq,'codexAuth':s.codex.snapshot(),'naverAuth':s.naver_auth()})
                return self.respond(200,s.snapshot())
            if path=='/api/posts':
                after=max(0,int(q.get('after',['0'])[0]));limit=max(1,min(500,int(q.get('limit',['500'])[0])))
                rows=s.db.stats(after,limit);return self.respond(200,{'items':rows,'next':rows[-1]['postId'] if len(rows)==limit else None})
            if path.startswith('/api/runs/'):
                rid=path[len('/api/runs/'):]
                if rid.endswith('/logs'):
                    return self.respond(200,{'items':s.db.logs(rid[:-5],max(0,int(q.get('after',['0'])[0])))})
                return self.respond(200,s.db.run(rid))
            if path.startswith('/api/artifacts/'):
                artifact=s.db.artifact(path.rsplit('/',1)[-1]);p=Path(artifact['path'])
                if artifact['kind'].startswith('preview:'):
                    return self.respond(200,p.read_bytes(),'image/png')
                kind=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
                return self.respond(200,p.read_bytes(),kind,{'Content-Disposition':"attachment; filename*=UTF-8''"+quote(p.name)})
            raise Problem('경로를 찾을 수 없습니다.',404)
        except Exception as exc:self.handle_error(exc)

    def do_POST(self):
        try:
            try:self.headers_ok(write=True)
            except Problem:
                self.discard_rejected_body()
                raise
            path=urlsplit(self.path).path;data=self.body();s=self.server.service
            if path=='/api/settings':
                cfg=validate(data.get('settings'));rev=s.db.save_settings(cfg,data.get('expectedRevision'))
                return self.respond(200,{'revision':rev,'settings':cfg})
            if path=='/api/runs':return self.respond(201,s.create_run(data.get('settings'),data.get('requestId')))
            if path.startswith('/api/runs/') and path.endswith('/stop'):
                rid=path[len('/api/runs/'):-len('/stop')];s.db.run_row(rid);s.db.stop(rid)
                return self.respond(200,{'message':'중지 요청을 저장했습니다. 진행 중인 작업의 안전한 종료를 기다립니다.'})
            if path=='/api/reviews':
                s.db.review(data.get('analysisId'),data.get('state','approved'),data.get('note',''));return self.respond(200,{'status':'saved'})
            if path=='/api/secrets':
                value=data.get('apiKey','')
                if not isinstance(value,str) or not 1<=len(value)<=1000 or any(c.isspace() for c in value):raise Problem('API 키 형식을 확인하세요.')
                s.secret=value;return self.respond(200,{'status':'memory_only'})
            if path=='/api/login':return self.respond(202,s.login(confirm=data.get('confirm') is True))
            if path=='/api/login/confirm':return self.respond(200,s.confirm_login())
            if path=='/api/login/cancel':return self.respond(202,s.cancel_login())
            if path=='/api/codex/login':return self.respond(202,s.codex_request('login'))
            if path=='/api/codex/check':return self.respond(202,s.codex_request('check'))
            if path in ('/api/choose-directory','/api/artifacts/link'):
                if os.name!='nt':raise Problem('폴더/파일 선택은 Windows에서 사용할 수 있습니다.')
                import tkinter as tk
                from tkinter import filedialog
                root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
                try:
                    if path=='/api/choose-directory':
                        selected=filedialog.askdirectory(parent=root,title='폴더 선택')
                        return self.respond(200,{'path':selected})
                    rid=data.get('runId');run=s.db.run_row(rid)
                    if run['state'] in ('queued','running','stopping'):raise Problem('실행이 끝난 뒤 PPT를 연결하세요.',409)
                    selected=filedialog.askopenfilename(parent=root,title='이 실행에 연결할 실제 PPT',filetypes=[('PowerPoint','*.pptx *.ppt')])
                finally:root.destroy()
                if not selected:return self.respond(200,{'status':'cancelled'})
                p=Path(selected).resolve()
                if p.suffix.lower() not in ('.pptx','.ppt'):raise Problem('PPT 파일을 선택하세요.')
                s.db.add_artifact(rid,'linked_ppt',p);s.db.update_run(rid,pptLinked=True,ppt=str(p))
                return self.respond(200,{'status':'linked'})
            if path=='/api/import':
                from .migrate import import_project
                with s.lock:
                    if s.child and s.child.poll() is None:raise Problem('현재 작업이 끝난 뒤 이관하세요.',409)
                    result=import_project(s.db,data.get('path',''))
                return self.respond(200,result)
            if path=='/api/backup':
                from .common import now
                p=s.db.backup(s.db.directory/'backups'/('monitoring_'+now().strftime('%Y%m%d_%H%M%S')+'.sqlite3'))
                return self.respond(200,{'path':str(p)})
            if path=='/api/outlook-account':
                from .office import outlook_account
                sender=outlook_account();s.db.put('outlook_sender',sender);return self.respond(200,{'sender':sender})
            if path=='/api/open-artifact':
                artifact=s.db.artifact(data.get('id',''))
                if os.name!='nt':raise Problem('Windows에서 파일/폴더를 열 수 있습니다.')
                p=Path(artifact['path'])
                if data.get('folder'):subprocess.Popen(['explorer.exe','/select,',str(p)])
                elif artifact['kind'] in ('ppt','summary_ppt','linked_ppt'):os.startfile(p)
                else:raise Problem('PPT 파일만 직접 열 수 있습니다.')
                return self.respond(200,{'status':'opened'})
            if path=='/api/mail/resolve':
                rid=data.get('runId');row=s.db.run_row(rid)
                if row['state']!='mail_unknown' or data.get('decision') not in ('confirmed_submitted','confirmed_not_sent'):raise Problem('발송 확인 기록을 확인하세요.')
                s.db.mail_finish(rid,data['decision'],{'message':'사용자가 Outlook에서 확인함'})
                s.db.update_run(rid,state='partial',notes='Outlook 확인: '+data['decision'])
                return self.respond(200,{'status':'recorded'})
            raise Problem('지원하지 않는 요청입니다.',404)
        except Exception as exc:self.handle_error(exc)
