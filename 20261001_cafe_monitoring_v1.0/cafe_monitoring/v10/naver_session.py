"""Share the chosen profile and protect session cookies across browser launches."""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import os
import time
from .common import ENGINE,Problem,stamp

AUTH_NAMES={'NID_AUT','NID_SES'}
LOGIN_MESSAGE='수집 브라우저에 네이버 로그인이 없습니다. 상단 네이버 로그인에서 로그인한 뒤 전용 창을 닫고 다시 실행하세요.'


def browser_settings(db,output=None):
    from v754.core import config
    from v754.refresh import browser_config
    legacy=Path(db.get('legacy_root')) if db.get('legacy_root') else None
    root=legacy if legacy and (legacy/'config_v5.json').is_file() else ENGINE
    paths=config(root);paths['out_root']=Path(output or db.directory/'output_v10')
    cfg=browser_config(root,paths)
    if root==ENGINE:cfg['profile_dir']=db.directory/'browser_profile_chrome'
    profile=cfg['profile_dir']
    cfg['profile_lock']=profile.parent/(profile.name+'_v5.lock')
    return cfg


def naver_cookies(cookies):
    result=[]
    for c in cookies:
        domain=str(c.get('domain','')).lower().lstrip('.')
        expires=c.get('expires',-1)
        if (domain=='naver.com' or domain.endswith('.naver.com')) and (expires==-1 or expires>time.time()):
            result.append(c)
    return result


def has_session(cookies):
    return AUTH_NAMES <= {c.get('name') for c in naver_cookies(cookies) if c.get('value')}


class SessionStore:
    """Windows user-bound encrypted cache; no credentials in DB/API/log/output."""
    def __init__(self,db,cfg):
        identity=os.path.normcase(str(Path(cfg['profile_dir']).resolve()))+'\n'+cfg['browser']
        self.identity=hashlib.sha256(identity.encode('utf-8')).hexdigest()
        self.path=db.directory/'naver_sessions'/(self.identity+'.dpapi')

    def save(self,cookies):
        cookies=naver_cookies(cookies)
        if not has_session(cookies):self.clear();return False
        payload=json.dumps({'profile':self.identity,'cookies':cookies},ensure_ascii=False).encode('utf-8')
        try:
            import win32crypt
            sealed=win32crypt.CryptProtectData(payload,'V10 Naver session',self.identity.encode(),None,None,1)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp')
            try:temp.write_bytes(sealed);temp.replace(self.path)
            finally:temp.unlink(missing_ok=True)
        except Exception:
            raise Problem('로그인 세션을 안전하게 저장하지 못했습니다. 설치 검사와 저장 폴더 권한을 확인하세요.',code='SESSION_SAVE_FAILED') from None
        return True

    def load(self):
        if not self.path.is_file():return []
        try:
            import win32crypt
            _,payload=win32crypt.CryptUnprotectData(self.path.read_bytes(),self.identity.encode(),None,None,1)
            data=json.loads(payload)
            if data['profile']!=self.identity:raise ValueError('profile mismatch')
            cookies=naver_cookies(data['cookies'])
        except Exception:
            raise Problem('저장된 로그인 세션을 불러오지 못했습니다. 네이버 로그인에서 다시 로그인하세요.',code='SESSION_LOAD_FAILED') from None
        return cookies if has_session(cookies) else []

    def clear(self):self.path.unlink(missing_ok=True)


class CollectionSession:
    def __init__(self,db,rid,context,cfg,gate):
        self.db,self.rid,self.context,self.gate=db,rid,context,gate
        self.store=SessionStore(db,cfg);self.rejected=False;self.listeners=[]

    def reject(self,message=LOGIN_MESSAGE):
        if self.rejected:return
        self.rejected=True;self.gate.stop('LOGIN_REQUIRED')
        self.store.clear()
        self.db.put('naver_auth',{'state':'unverified','checkedAt':None,'message':message})
        self.db.log(self.rid,message,'ERROR')

    def watch_page(self,page):
        def navigation(frame):
            if frame!=page.main_frame:return
            url=urlsplit(frame.url)
            if url.hostname=='nid.naver.com' and url.path.startswith('/nidlogin'):
                self.reject('수집 중 네이버 로그인 창이 나타나 새 요청을 중단했습니다. 상단 네이버 로그인에서 다시 로그인하세요.')
        page.on('framenavigated',navigation);self.listeners.append((page,navigation))
        navigation(page.main_frame)

    def detach(self):
        self.context.remove_listener('page',self.watch_page)
        for page,callback in self.listeners:page.remove_listener('framenavigated',callback)
        self.listeners=[]

    async def __aenter__(self):
        self.db.log(self.rid,'수집 브라우저의 로그인 세션을 확인합니다.')
        self.context.on('page',self.watch_page)
        try:
            for page in self.context.pages:self.watch_page(page)
            if self.rejected:raise Problem(LOGIN_MESSAGE,code='LOGIN_REQUIRED')
            cookies=await self.context.cookies('https://cafe.naver.com')
            if not has_session(cookies):
                saved=self.store.load()
                if saved:
                    await self.context.add_cookies(saved)
                    cookies=await self.context.cookies('https://cafe.naver.com')
            if not has_session(cookies):
                self.reject();raise Problem(LOGIN_MESSAGE,code='LOGIN_REQUIRED')
            self.db.put('naver_auth',{'state':'session_present','checkedAt':stamp(),'message':'수집 브라우저에서 로그인 세션을 확인했습니다. 실제 접근 권한은 수집 중 확인합니다.'})
            self.db.log(self.rid,'로그인 세션 확인 완료 · 카페 수집을 시작합니다.')
            return self
        except BaseException as exc:
            if not self.rejected:self.reject('로그인 세션을 확인하지 못했습니다. 네이버 로그인에서 다시 로그인하세요.')
            self.detach()
            if isinstance(exc,Exception) and not isinstance(exc,Problem):
                raise Problem('수집 브라우저의 로그인 세션을 불러오지 못했습니다. 다시 로그인하세요.',code='SESSION_LOAD_FAILED') from None
            raise

    async def __aexit__(self,exc_type,exc,tb):
        try:
            if getattr(exc,'code','') in ('LOGIN_REQUIRED','AUTH_REQUIRED'):
                self.reject()
            if not self.rejected:
                cookies=await self.context.cookies()
                if has_session(cookies):self.store.save(cookies)
                else:self.reject()
        except Exception:
            self.db.log(self.rid,'종료 시 로그인 세션을 저장하지 못했습니다. 다음 실행 전 로그인 상태를 확인하세요.','WARNING')
        finally:self.detach()
