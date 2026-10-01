"""Local Codex login UI bridge; credentials stay with the official CLI."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from .common import ENGINE, Problem, stamp


def load_runtime(db):
    # Match LegacyEngine's config-file fallback and the analysis adapter exactly.
    if str(ENGINE) not in sys.path:sys.path.insert(0,str(ENGINE))
    from v9.ai_provider import load_settings, codex_options, adapter_module
    legacy=Path(db.get('legacy_root')) if db.get('legacy_root') else None
    root=legacy if legacy and (legacy/'config_ai_v95.json').is_file() else ENGINE
    settings=load_settings(root)
    settings['provider']='codex'
    settings['codex']['acknowledge_cli_differences']=True
    return adapter_module().CodexRuntime(codex_options(settings))


def login_process_options():
    if os.name!='nt':raise Problem('Codex 로그인 창은 Windows PC에서 열 수 있습니다.',400,'WINDOWS_REQUIRED')
    return {'creationflags':subprocess.CREATE_NEW_CONSOLE}


class CodexAuth:
    LOGIN_TIMEOUT=600

    def __init__(self,db):
        self.db=db
        self.lock=threading.RLock()
        self.closed=threading.Event()
        self.thread=None
        self.runtime=None
        self.task=None
        self.state={'state':'unchecked','message':'Codex 로그인 상태 확인 전','checkedAt':None}

    def snapshot(self):
        with self.lock:return {**self.state,'busy':self.task is not None}

    def login_active(self):
        with self.lock:return self.task=='login'

    def _set(self,state,message,code=None):
        with self.lock:
            self.state={'state':state,'message':message,'checkedAt':stamp()}
            if code:self.state['code']=code

    def request(self,action):
        # action is selected by fixed server routes, never a client command.
        if action not in ('login','check'):raise ValueError('Unknown Codex action')
        with self.lock:
            if self.closed.is_set():raise Problem('V10 서버를 다시 실행하세요.',409)
            if self.task:
                if action=='login' and self.task!='login':
                    raise Problem('Codex 상태 확인이 끝난 뒤 로그인하세요.',409)
                return self.snapshot()
            self.task=action
            self._set('checking' if action=='check' else 'starting',
                      'Codex 로그인 상태를 확인하고 있습니다.' if action=='check' else 'Codex 로그인 창을 준비하고 있습니다.')
            self.thread=threading.Thread(target=self._work,args=(action,),daemon=True)
            try:self.thread.start()
            except Exception:
                self.task=None
                self._set('error','Codex 작업을 시작하지 못했습니다. 다시 시도하세요.','CODEX_START_FAILED')
            return self.snapshot()

    def _check(self,runtime,args):
        # _probe uses a bounded temporary file and removes it on exit.
        code,output=runtime._probe([*args,'login','status'])
        low=output.lower()
        if code==0 and 'logged in using chatgpt' in low:
            self._set('connected','ChatGPT 로그인 확인 완료 · AI 분석에 사용합니다.')
        elif code==0:
            self._set('login_required','ChatGPT 방식으로 로그인하세요. Codex 로그인 버튼을 누르세요.','CODEX_LOGIN_REQUIRED')
        elif 'not logged in' in low or 'login required' in low:
            self._set('login_required','Codex 로그인이 필요합니다. 로그인 버튼을 누르세요.','CODEX_LOGIN_REQUIRED')
        else:
            self._set('error','Codex 로그인 상태를 확인하지 못했습니다. 로그인 후 상태 확인을 눌러주세요.','CODEX_STATUS_FAILED')

    def _login(self,runtime,args):
        from v9.codex_adapter.runtime import child_environment, _kill_tree
        options=login_process_options()
        # A new Windows console keeps the official login URL visible if the
        # browser cannot open automatically. No shell or command interpolation.
        with tempfile.TemporaryDirectory(prefix='v10-codex-login-') as folder:
            if self.closed.is_set():return
            process=subprocess.Popen([*runtime._prefix,*args,'login'],cwd=folder,
                                     env=child_environment(runtime.options),shell=False,**options)
            self._set('login_pending','열린 브라우저에서 ChatGPT 로그인을 완료하세요. 완료 후 자동으로 확인합니다.')
            deadline=time.monotonic()+self.LOGIN_TIMEOUT
            try:
                while process.poll() is None:
                    if self.closed.wait(0.2):return
                    if time.monotonic()>=deadline:
                        self._set('error','로그인 대기 시간이 끝났습니다. 로그인 버튼으로 다시 시도하세요.','CODEX_LOGIN_TIMEOUT')
                        return
                if self.closed.is_set():return
                if process.returncode!=0:
                    self._set('error','Codex 로그인이 완료되지 않았습니다. 로그인 버튼으로 다시 시도하세요.','CODEX_LOGIN_INCOMPLETE')
                    return
                self._check(runtime,args)
            finally:
                if process.poll() is None:_kill_tree(process)

    def _work(self,action):
        runtime=None
        try:
            runtime=load_runtime(self.db)
            from v9.codex_adapter.runtime import resolve_command
            with self.lock:
                self.runtime=runtime
                if self.closed.is_set():return
            runtime._prefix=resolve_command(runtime.options)
            args=['-c','forced_login_method="chatgpt"','-c',
                  'cli_auth_credentials_store='+json.dumps(runtime.options.credentials_store)]
            if action=='login':self._login(runtime,args)
            else:self._check(runtime,args)
        except Exception as exc:
            # Never put CLI output, environment, auth URLs or exception text in
            # the DB, HTTP state or worker logs.
            code=getattr(exc,'code','CODEX_AUTH_ERROR')
            messages={
                'CODEX_NOT_FOUND':'Codex CLI를 찾지 못했습니다. Codex를 설치한 뒤 V10을 다시 실행하세요.',
                'CODEX_WRAPPER_UNSUPPORTED':'Codex 실행 파일을 확인하지 못했습니다. 기존 AI 설정의 실행 경로를 확인하세요.',
                'CODEX_DEPENDENCY_MISSING':'Codex 연결 패키지가 없습니다. 01_setup_v10.bat를 실행하세요.',
                'AI_CONFIG_ERROR':'기존 AI 설정 파일을 확인하세요. Codex 연결 설정을 읽지 못했습니다.',
                'CODEX_TIMEOUT':'Codex 상태 확인 시간이 초과됐습니다. 상태 확인을 다시 눌러주세요.',
                'WINDOWS_REQUIRED':'Codex 로그인 창은 Windows PC에서 열 수 있습니다.',
            }
            if code not in messages:code='CODEX_AUTH_ERROR'
            self._set('error',messages.get(code,'Codex 인증 작업을 마치지 못했습니다. 상태 확인 후 다시 로그인하세요.'),code)
        finally:
            if runtime:runtime.close()
            with self.lock:self.runtime=None;self.task=None

    def close(self):
        self.closed.set()
        with self.lock:
            if self.runtime:self.runtime.close()
            thread=self.thread
        if thread:thread.join(timeout=8)
