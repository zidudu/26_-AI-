"""V8 beta 인증. 신뢰 경계: loopback 전용 서버 + 명시적 HTTPS 프록시 주소.

Uvicorn proxy_headers=False와 함께 사용합니다. localhost도 인증을 생략하지 않습니다.
개인 사용 단일 계정이며, 인터넷 공개를 위한 범용 다중 사용자 서비스가 아닙니다.
"""
from __future__ import annotations
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager, closing
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parents[1]
LOCAL_COOKIE = 'yme_beta_local'
REMOTE_COOKIE = '__Host-yme_beta'
FAST_COOKIE = '__Host-yme_beta_fast'
MAX_BODY = 1_000_000
REMEMBER_DAYS = 30


def https_origin(value: str, private: bool = False, fast: bool = False) -> str:
    value = value.strip().rstrip('/')
    if not value:
        return ''
    p = urlsplit(value)
    try:
        port = p.port
    except ValueError:
        raise ValueError('외부 주소의 포트가 잘못되었습니다.') from None
    if (p.scheme != 'https' or not p.hostname or p.username or p.password or p.path
            or p.query or p.fragment or port not in ({8443} if fast else {None, 443})):
        raise ValueError('외부 주소는 경로 없는 https://호스트명 형식이어야 합니다.')
    host = p.hostname.lower()
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', host) or '..' in host:
        raise ValueError('유효한 영문 도메인 이름을 입력해 주세요.')
    if host == 'localhost' or host.endswith('.localhost') or '.' not in host:
        raise ValueError('외부 주소에는 HTTPS 도메인을 입력해 주세요.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('외부 주소에 IP 대신 HTTPS 도메인을 입력해 주세요.')
    if (private or fast) and not host.endswith('.ts.net'):
        raise ValueError('개인 접속 주소는 Tailscale의 .ts.net HTTPS 주소여야 합니다.')
    return 'https://' + host + (':8443' if fast else '')


@dataclass
class ServerConfig:
    port: int = 8765
    private_url: str = ''
    cloudflare_url: str = ''
    fast_url: str = ''
    data_dir: str = str(ROOT / 'data')
    output_dir: str = str(ROOT / 'output')
    session_hours: int = 12
    idle_minutes: int = 60

    def checked(self):
        if not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            raise ValueError('포트는 1024~65535 범위여야 합니다.')
        self.private_url = https_origin(self.private_url, True)
        self.cloudflare_url = https_origin(self.cloudflare_url)
        self.fast_url = https_origin(self.fast_url, fast=True)
        if self.private_url and self.private_url == self.cloudflare_url:
            raise ValueError('두 외부 주소는 서로 달라야 합니다.')
        if not 1 <= self.session_hours <= 24 or not 5 <= self.idle_minutes <= 120:
            raise ValueError('세션 설정이 허용 범위를 벗어났습니다.')
        self.data_dir = str(Path(self.data_dir).expanduser().resolve())
        self.output_dir = str(Path(self.output_dir).expanduser().resolve())
        return self

    @classmethod
    def load(cls, folder: Path):
        path = folder / 'server.json'
        if not path.is_file():
            raise RuntimeError('먼저 01_setup_beta.bat으로 서버를 설정해 주세요.')
        return cls(**json.loads(path.read_text(encoding='utf-8-sig'))).checked()

    def save(self, folder: Path):
        self.checked()
        folder.mkdir(parents=True, exist_ok=True)
        atomic_private_json(folder / 'server.json', asdict(self))


def atomic_private_json(path: Path, data: dict):
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        os.chmod(tmp, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def password_record(password: str):
    if not 8 <= len(password) <= 128:
        raise ValueError('비밀번호는 8~128자로 정해 주세요.')
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8,
                         p=1, dklen=32, maxmem=64*1024*1024)
    return {'algorithm': 'scrypt-v1', 'salt': salt.hex(), 'digest': key.hex()}


def password_matches(password: str, record: dict):
    if not 1 <= len(password) <= 128 or record.get('algorithm') != 'scrypt-v1':
        return False
    key = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(record['salt']),
                         n=32768, r=8, p=1, dklen=32, maxmem=64*1024*1024)
    return hmac.compare_digest(key.hex(), record['digest'])


def set_owner(folder: Path, username: str, password: str):
    username = username.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}', username):
        raise ValueError('아이디는 영문·숫자로 시작하는 3~80자입니다. _, ., @, -도 가능합니다.')
    folder.mkdir(parents=True, exist_ok=True)
    atomic_private_json(folder / 'owner.json', {
        'username': username, 'revision': secrets.token_hex(16), 'password': password_record(password)})
    if (folder / 'security.sqlite3').is_file():
        with closing(sqlite3.connect(folder / 'security.sqlite3', timeout=10)) as c, c:
            c.execute('DELETE FROM sessions')
            c.execute('DELETE FROM attempts')


def loopback(value: str):
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


class Security:
    def __init__(self, config: ServerConfig, folder: Path):
        self.config = config.checked()
        self.folder = folder.resolve()
        self.db = self.folder / 'security.sqlite3'
        self.started = time.monotonic()
        self.login_slots = threading.BoundedSemaphore(2)
        self.read_owner()
        with self.connect() as c:
            c.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                  digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, origin TEXT NOT NULL,
                  revision TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, seen REAL NOT NULL,
                  remembered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS attempts (at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_at ON attempts(at);
                CREATE TABLE IF NOT EXISTS audit (at REAL NOT NULL, event TEXT NOT NULL, mode TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in c.execute('PRAGMA table_info(sessions)')}
            if 'remembered' not in columns:
                c.execute('ALTER TABLE sessions ADD COLUMN remembered INTEGER NOT NULL DEFAULT 0')
        os.chmod(self.db, 0o600)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.db, timeout=10)) as c, c:
            c.row_factory = sqlite3.Row
            yield c

    def read_owner(self):
        path = self.folder / 'owner.json'
        if not path.is_file():
            raise RuntimeError('소유자 계정이 없습니다. 01_setup_beta.bat을 실행하세요.')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not data.get('revision') or data.get('password', {}).get('algorithm') != 'scrypt-v1':
            raise RuntimeError('계정 설정이 잘못되었습니다. 서버 PC에서 계정을 재설정하세요.')
        return data

    def context(self, request: Request):
        if not loopback(request.client.host if request.client else ''):
            raise HTTPException(403, '로컬 역방향 프록시를 통해 접속해 주세요.')
        host = request.headers.get('host', '').lower()
        if not host or any(x in host for x in ('/', '\\', '@', ',', ' ', '\t')):
            raise HTTPException(403, '허용되지 않은 Host입니다.')
        local_hosts = {f'127.0.0.1:{self.config.port}', f'localhost:{self.config.port}'}
        if host in local_hosts:
            # localhost 위장 전달을 거부합니다. Tunnel은 외부 Host를 보존해야 합니다.
            if any(request.headers.get(k) for k in (
                'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto',
                'cf-connecting-ip', 'cf-ray', 'tailscale-user-login')):
                raise HTTPException(403, '프록시에서 외부 HTTPS Host를 보존해 주세요.')
            return 'http://' + host, 'local', LOCAL_COOKIE
        for url, kind in ((self.config.private_url, 'tailscale'), (self.config.fast_url, 'tailscale_fast'), (self.config.cloudflare_url, 'cloudflare')):
            hosts = {urlsplit(url).netloc}
            if url and urlsplit(url).port in (None, 443):
                hosts.add(urlsplit(url).netloc + ':443')
            if url and host in hosts:
                if request.headers.get('x-forwarded-proto', 'https').lower() != 'https':
                    raise HTTPException(403, '외부 접속에는 HTTPS가 필요합니다.')
                return url, kind, FAST_COOKIE if kind == 'tailscale_fast' else REMOTE_COOKIE
        raise HTTPException(403, '서버 설정에 등록되지 않은 접속 주소입니다.')

    def session(self, value: str, origin: str, touch: bool = True):
        if not value or len(value) > 160:
            return None
        digest = hashlib.sha256(value.encode()).hexdigest()
        now = time.time()
        with self.connect() as c:
            row = c.execute('SELECT * FROM sessions WHERE digest=?', (digest,)).fetchone()
            if not row:
                return None
            idle_expired = (not row['remembered'] and now - row['seen'] > self.config.idle_minutes*60)
            if (row['origin'] != origin or row['expires'] <= now
                    or idle_expired
                    or row['revision'] != self.read_owner()['revision']):
                # 다른 origin에서 사용한 토큰도 무효화합니다.
                c.execute('DELETE FROM sessions WHERE digest=?', (digest,))
                return None
            if touch and now - row['seen'] > 30:
                c.execute('UPDATE sessions SET seen=? WHERE digest=?', (now, digest))
            return dict(row)

    def audit(self, event: str, mode: str):
        with self.connect() as c:
            c.execute('INSERT INTO audit VALUES (?,?,?)', (time.time(), event, mode))
            c.execute('DELETE FROM audit WHERE rowid NOT IN (SELECT rowid FROM audit ORDER BY rowid DESC LIMIT 1000)')

    def login(self, username: str, password: str, origin: str, mode: str, remember: bool = False):
        # 개인 서버의 전역 제한입니다. IP 헤더를 바꿔도 제한을 우회하지 못합니다.
        if not self.login_slots.acquire(blocking=False):
            raise HTTPException(429, '로그인 처리 중입니다. 잠시 후 다시 시도하세요.')
        try:
            now = time.time()
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('DELETE FROM attempts WHERE at<?', (now-300,))
                if c.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 8:
                    raise HTTPException(429, '로그인 시도가 많습니다. 5분 후 다시 시도하세요.')
                c.execute('INSERT INTO attempts VALUES (?)', (now,))
            owner = self.read_owner()
            password_ok = password_matches(password, owner['password'])
            if not (hmac.compare_digest(username.encode(), owner['username'].encode()) and password_ok):
                self.audit('login_failed', mode)
                raise HTTPException(401, '아이디 또는 비밀번호가 맞지 않습니다.')
            token = secrets.token_urlsafe(48)
            with self.connect() as c:
                c.execute('DELETE FROM sessions WHERE expires<? OR revision!=?', (now, owner['revision']))
                c.execute('DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions ORDER BY created DESC LIMIT -1 OFFSET 23)')
                lifetime = REMEMBER_DAYS*86400 if remember else self.config.session_hours*3600
                c.execute('''INSERT INTO sessions
                    (digest,csrf,origin,revision,created,expires,seen,remembered)
                    VALUES (?,?,?,?,?,?,?,?)''', (
                    hashlib.sha256(token.encode()).hexdigest(), secrets.token_urlsafe(32), origin,
                    owner['revision'], now, now+lifetime, now, 1 if remember else 0))
            self.audit('login_success_remembered' if remember else 'login_success', mode)
            return token
        finally:
            self.login_slots.release()

    def install(self, app):
        app.state.security = self
        app.state.remote_config = self.config

        def secure_headers(response, request):
            cache_policy = (getattr(request.state, 'cache_policy', None)
                            if response.status_code in {200, 304} else None)
            response.headers.update({
                'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                'Referrer-Policy': 'no-referrer', 'Cache-Control': cache_policy or 'private, no-store, max-age=0',
                'Pragma': 'no-cache', 'Vary': 'Cookie',
                'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'",
                'Permissions-Policy': 'camera=(), microphone=(), geolocation=()'})
            if cache_policy:
                del response.headers['pragma']
            if getattr(request.state, 'connection_mode', 'local') != 'local':
                response.headers['Strict-Transport-Security'] = 'max-age=31536000'
            return response

        @app.middleware('http')
        async def secure_requests(request: Request, call_next):
            try:
                origin, mode, cookie = self.context(request)
                request.state.connection_mode = mode
                request.state.effective_origin = origin
                request.state.cookie_name = cookie
                incoming = request.headers.get('origin')
                if incoming and incoming != origin:
                    raise HTTPException(403, '다른 사이트에서 보낸 요청을 차단했습니다.')
                if request.headers.get('sec-fetch-site') == 'cross-site' and not (
                    request.method in {'GET', 'HEAD'} and request.url.path in {'/', '/login'}):
                    raise HTTPException(403, '외부 사이트의 요청을 차단했습니다.')
                if request.method not in {'GET', 'HEAD'}:
                    if incoming != origin:
                        raise HTTPException(403, '동일 출처 Origin이 필요합니다. 화면을 새로고침하세요.')
                    chunks = bytearray()
                    async for chunk in request.stream():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BODY:
                            raise HTTPException(413, '요청이 너무 큽니다.')
                    request._body = bytes(chunks)
                public = request.url.path in {'/login', '/auth/login', '/api/health',
                                              '/static/login.css', '/static/login.js', '/static/favicon.svg'}
                record = await run_in_threadpool(self.session, request.cookies.get(cookie, ''), origin)
                request.state.auth_session = record
                if not record and not public:
                    if request.url.path == '/':
                        target = '/login'
                        incoming_url = request.query_params.get('add', '')
                        if incoming_url and len(incoming_url) <= 2048:
                            target += '?' + urlencode({'add': incoming_url})
                        return secure_headers(RedirectResponse(target, status_code=303), request)
                    raise HTTPException(401, '로그인이 필요합니다.')
                if request.method not in {'GET', 'HEAD'} and not public:
                    if not hmac.compare_digest(request.headers.get('x-yme-token', ''), record['csrf']):
                        raise HTTPException(403, '보안 토큰이 만료되었습니다. 화면을 새로고침하세요.')
                if request.url.path.startswith('/static/') and record:
                    request.state.cache_policy = 'private, no-cache, must-revalidate'
                response = await call_next(request)
            except HTTPException as exc:
                response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
                if exc.status_code == 429:
                    response.headers['Retry-After'] = '300'
            if response.status_code == 429:
                response.headers['Retry-After'] = '300'
            return secure_headers(response, request)

        @app.get('/login')
        def login_page(request: Request):
            if request.state.auth_session:
                return RedirectResponse('/', status_code=303)
            return FileResponse(ROOT / 'static/login.html', media_type='text/html')

        @app.post('/auth/login')
        async def login(request: Request):
            if 'application/json' not in request.headers.get('content-type', ''):
                raise HTTPException(415, 'JSON 형식이 필요합니다.')
            raw = await request.body()
            if len(raw) > 4096:
                raise HTTPException(413, '입력이 너무 큽니다.')
            try:
                body = json.loads(raw)
                user, password = body['username'], body['password']
                remember = body.get('remember', False)
                if (not isinstance(user, str) or not isinstance(password, str)
                        or not isinstance(remember, bool) or len(user)>80 or len(password)>128):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise HTTPException(400, '로그인 입력을 확인하세요.') from None
            value = await run_in_threadpool(self.login, user, password,
                                            request.state.effective_origin, request.state.connection_mode, remember)
            response = JSONResponse({'ok': True, 'remembered': remember})
            cookie_options = dict(httponly=True, secure=request.state.connection_mode != 'local',
                                  samesite='strict', path='/')
            if remember:
                cookie_options['max_age'] = REMEMBER_DAYS*86400
            response.set_cookie(request.state.cookie_name, value, **cookie_options)
            return response

        @app.post('/api/auth/logout')
        def logout(request: Request):
            with self.connect() as c:
                c.execute('DELETE FROM sessions WHERE digest=?', (request.state.auth_session['digest'],))
            response = JSONResponse({'ok': True})
            response.delete_cookie(request.state.cookie_name, path='/', httponly=True,
                                   secure=request.state.connection_mode != 'local', samesite='strict')
            return response

        @app.post('/api/auth/logout-all')
        def logout_all(request: Request):
            with self.connect() as c:
                c.execute('DELETE FROM sessions')
            self.audit('logout_all', request.state.connection_mode)
            response = JSONResponse({'ok': True})
            response.delete_cookie(request.state.cookie_name, path='/', httponly=True,
                                   secure=request.state.connection_mode != 'local', samesite='strict')
            return response


def require_local(request: Request):
    if request.state.connection_mode != 'local':
        raise HTTPException(403, '이 기능은 서버 PC의 localhost 화면에서만 사용할 수 있습니다.')
