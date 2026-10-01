"""Real child-process contracts using a local CLI double; no OpenAI login."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from v10.common import ENGINE,Problem,read_json,write_json
from v10.codex_auth import load_runtime
from v10.service import Service
from v10.settings import defaults

CLI = r'''
from pathlib import Path
import json, os, sys, time
root=Path(__file__).parent
cfg=json.loads((root/'fake.json').read_text())
with (root/'calls.jsonl').open('a') as f:
    f.write(json.dumps({'args':sys.argv[1:], 'home':os.environ.get('CODEX_HOME'),
        'has_api_key':any(x in os.environ for x in ('OPENAI_API_KEY','CODEX_API_KEY'))})+'\n')
if sys.argv[-1]=='status':
    if cfg.get('slow_status'):time.sleep(30)
    if cfg.get('status_delay'):time.sleep(cfg['status_delay'])
    status=cfg.get('status') or ('chatgpt' if (root/'authenticated').exists() else 'none')
    print({'chatgpt':'Logged in using ChatGPT','api':'Logged in using an API key',
           'none':'Not logged in','error':'private-error-secret'}[status],file=sys.stderr)
    sys.exit(0 if status in ('chatgpt','api') else 1)
assert sys.argv[-1]=='login'
(root/'started').touch()
while cfg.get('wait') and not (root/'release').exists():time.sleep(0.02)
if cfg.get('fail'):sys.exit(2)
(root/'authenticated').touch()
'''


class CodexAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.s=Service(self.root/'data',launch=False)
        self.addCleanup(self.s.codex.close)
        # Imports use exactly the bundled provider/adapter, not a second CLI resolver.
        load_runtime(self.s.db).close()
        from v9.codex_adapter.config import CodexOptions
        from v9.codex_adapter.runtime import CodexRuntime
        self.script=self.root/'fake_cli.py';self.script.write_text(CLI,encoding='utf-8')
        # Keep the production allowance: the user's Windows control needed
        # about 3 seconds for this CLI double, beyond the former 2-second cap.
        self.options=CodexOptions(command_prefix=(sys.executable,str(self.script)),
            credentials_store='file',codex_home=self.root/'auth_home')
        self.factory=lambda db:CodexRuntime(self.options)
        self.configure()

    def configure(self,**cfg):write_json(self.root/'fake.json',cfg)

    def wait(self,done=None):
        allowance=self.options.preflight_timeout_seconds+10
        deadline=time.monotonic()+allowance
        while time.monotonic()<deadline:
            if done() if done else not self.s.codex.snapshot()['busy']:return
            time.sleep(0.01)
        self.fail(f'Codex test did not finish within {allowance:g} seconds: {self.s.codex.snapshot()}')

    def assert_auth(self,state,code=None):
        snapshot=self.s.codex.snapshot()
        self.assertEqual((snapshot['state'],snapshot.get('code'),snapshot['busy']),
                         (state,code,False),msg=snapshot)

    def calls(self):
        p=self.root/'calls.jsonl'
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def start(self,action):
        return self.s.codex_request(action)

    def process_options(self):
        # No console/browser in offline tests; new process group allows Linux cleanup.
        return {} if os.name=='nt' else {'start_new_session':True}

    def test_bundled_and_legacy_auth_settings_match_analysis(self):
        from v9.ai_provider import codex_options
        from v10.engine import LegacyEngine
        from types import SimpleNamespace
        legacy=self.root/'legacy';legacy.mkdir();self.s.db.put('legacy_root',str(legacy))
        bundled=load_runtime(self.s.db).options
        self.assertEqual(bundled.command,read_json(ENGINE/'config_ai_v95.json')['codex']['command'])
        settings=read_json(ENGINE/'config_ai_v95.json')
        settings['codex'].update(command='custom-codex.exe',credentials_store='keyring')
        write_json(legacy/'config_ai_v95.json',settings)
        before=(legacy/'config_ai_v95.json').read_bytes()
        folder=self.root/'run';folder.mkdir();cfg=defaults()
        with patch('v10.engine.os',SimpleNamespace(name='nt')):
            engine=LegacyEngine(self.s.db,'auth-options',cfg,folder)
        self.assertEqual(load_runtime(self.s.db).options,codex_options(engine.cfg['v95_ai_settings']))
        self.assertEqual((legacy/'config_ai_v95.json').read_bytes(),before)

    def test_status_uses_real_process_and_distinguishes_auth_methods(self):
        with patch('v10.codex_auth.load_runtime',self.factory):
            for mode,state,code in [('none','login_required','CODEX_LOGIN_REQUIRED'),
                                    ('chatgpt','connected',None),
                                    ('api','login_required','CODEX_LOGIN_REQUIRED'),
                                    ('error','error','CODEX_STATUS_FAILED')]:
                with self.subTest(mode=mode):
                    self.configure(status=mode);self.start('check');self.wait()
                    self.assert_auth(state,code)
        self.assertTrue(all(c['args'][-2:]==['login','status'] for c in self.calls()))
        self.assertNotIn('private-error-secret',json.dumps(self.s.snapshot()))

    def test_three_second_status_exceeds_old_cap_but_passes_production_allowance(self):
        from dataclasses import replace
        from v9.codex_adapter.runtime import CodexRuntime
        self.configure(status='chatgpt',status_delay=3)
        old_cap=replace(self.options,preflight_timeout_seconds=2)
        with patch('v10.codex_auth.load_runtime',lambda db:CodexRuntime(old_cap)):
            self.start('check');self.wait()
        self.assert_auth('error','CODEX_TIMEOUT')
        with patch('v10.codex_auth.load_runtime',self.factory):
            self.start('check');self.wait()
        self.assert_auth('connected')

    def test_login_pending_then_verified_with_shared_environment_and_no_secrets(self):
        self.configure(wait=True)
        with patch('v10.codex_auth.load_runtime',self.factory), \
             patch('v10.codex_auth.login_process_options',self.process_options), \
             patch.dict(os.environ,{'OPENAI_API_KEY':'private-api-secret','CODEX_API_KEY':'private-codex-secret'}):
            self.start('login');self.wait(lambda:(self.root/'started').exists())
            self.assertEqual(self.s.codex.snapshot()['state'],'login_pending',msg=self.s.codex.snapshot())
            self.assertFalse((self.root/'authenticated').exists())
            (self.root/'release').touch();self.wait()
        self.assert_auth('connected')
        calls=self.calls();self.assertEqual(len(calls),2)
        self.assertEqual(calls[0]['args'][-1: ],['login'])
        self.assertEqual(calls[1]['args'][-2:],['login','status'])
        for c in calls:
            self.assertIn('cli_auth_credentials_store="file"',c['args'])
            self.assertIn('forced_login_method="chatgpt"',c['args'])
            self.assertEqual(c['home'],str(self.options.codex_home))
            self.assertFalse(c['has_api_key']);self.assertNotIn('exec',c['args'])
        state=json.dumps(self.s.snapshot())
        self.assertNotIn('private-api-secret',state)
        self.assertNotIn('private-codex-secret',state)
        self.assertIsNone(self.s.db.get('codex_auth'))

    def test_concurrent_clicks_open_only_one_login_and_block_run(self):
        self.configure(wait=True)
        with patch('v10.codex_auth.load_runtime',self.factory),patch('v10.codex_auth.login_process_options',self.process_options):
            with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:self.start('login'),range(12)))
            self.wait(lambda:(self.root/'started').exists())
            cfg=defaults();cfg.update(sendMail=False)
            with self.assertRaises(Problem) as cm:self.s.create_run(cfg,'while-login')
            self.assertEqual(cm.exception.code,'CODEX_LOGIN_BUSY')
            self.assertEqual(self.s.db.runs(),[])
            self.start('check')
            (self.root/'release').touch();self.wait()
        self.assert_auth('connected')
        self.assertEqual(sum(c['args'][-1]=='login' for c in self.calls()),1)

    def test_failed_login_never_promotes_old_credentials(self):
        self.configure(fail=True,status='chatgpt')
        with patch('v10.codex_auth.load_runtime',self.factory),patch('v10.codex_auth.login_process_options',self.process_options):
            self.start('login');self.wait()
        self.assert_auth('error','CODEX_LOGIN_INCOMPLETE')
        self.assertEqual(len(self.calls()),1)

    def test_login_timeout_cleans_owned_process(self):
        self.configure(wait=True);self.s.codex.LOGIN_TIMEOUT=0.05
        with patch('v10.codex_auth.load_runtime',self.factory),patch('v10.codex_auth.login_process_options',self.process_options):
            self.start('login');self.wait()
        self.assert_auth('error','CODEX_LOGIN_TIMEOUT')
        (self.root/'release').touch()
        self.assertFalse((self.root/'authenticated').exists())

    def test_close_cancels_pending_login_and_restart_does_not_show_green(self):
        self.configure(wait=True)
        with patch('v10.codex_auth.load_runtime',self.factory),patch('v10.codex_auth.login_process_options',self.process_options):
            self.start('login');self.wait(lambda:(self.root/'started').exists())
            self.s.codex.close()
        self.assertFalse(self.s.codex.thread.is_alive())
        self.assertFalse(self.s.codex.snapshot()['busy'])
        other=Service(self.s.db.directory,launch=False)
        self.assertEqual(other.codex.snapshot()['state'],'unchecked')

    def test_missing_cli_and_probe_timeout_are_explicit_and_redacted(self):
        from dataclasses import replace
        from v9.codex_adapter.runtime import CodexRuntime
        missing=replace(self.options,command_prefix=None,command=str(self.root/'missing-codex'))
        with patch('v10.codex_auth.load_runtime',lambda db:CodexRuntime(missing)):
            self.start('check');self.wait()
        self.assert_auth('error','CODEX_NOT_FOUND')
        self.configure(slow_status=True)
        timeout=replace(self.options,preflight_timeout_seconds=0.1)
        with patch('v10.codex_auth.load_runtime',lambda db:CodexRuntime(timeout)):
            self.start('check');self.wait()
        self.assert_auth('error','CODEX_TIMEOUT')

    def test_active_run_rejects_login_without_starting_cli(self):
        cfg=defaults();cfg.update(sendMail=False)
        self.s.create_run(cfg,'active-run')
        with patch('v10.codex_auth.load_runtime',self.factory) as load:
            with self.assertRaises(Problem) as cm:self.start('login')
        self.assertEqual(cm.exception.code,'RUN_ACTIVE')
        self.assertEqual(self.calls(),[])


if __name__=='__main__':unittest.main()
