"""SDK 예외를 주입하는 오프라인 검사. 실제 서버/회사 네트워크 검사 아님."""
import io
import json
import ssl
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from v753.api_diagnostics import APIError, exception_details, run_sdk_call
from v753.analysis_engine import analyze_articles
from v753.analysis_config import load_analysis_config
from v753.analysis_prompts import make_spec
from v753.core import read_json, write_json
from v753.diagnose_api import probe_models
from v753.tests.test_analysis import FakeAnalyzer, candidate_for, make_item, response_for


def sdk_fixture():
    module = SimpleNamespace()
    module.OpenAIError = type('OpenAIError', (Exception,), {})
    module.APIConnectionError = type('APIConnectionError', (module.OpenAIError,), {})
    module.APITimeoutError = type('APITimeoutError', (module.APIConnectionError,), {})
    module.APIStatusError = type('APIStatusError', (module.OpenAIError,), {})
    for name in ('AuthenticationError', 'PermissionDeniedError', 'NotFoundError', 'RateLimitError'):
        setattr(module, name, type(name, (module.APIStatusError,), {}))
    return module


class APIDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.sdk = sdk_fixture()
        self.calls = 0

    def fail(self, error):
        def operation():
            self.calls += 1
            raise error
        with patch.dict('sys.modules', {'openai': self.sdk}):
            with self.assertRaises(APIError) as context:
                run_sdk_call(operation, 120)
        return context.exception

    def test_timeout_is_distinct_from_connection_and_not_retried(self):
        error = self.fail(self.sdk.APITimeoutError('mock'))
        self.assertEqual(error.code, 'API_TIMEOUT')
        self.assertTrue(error.uncertain)
        self.assertEqual(self.calls, 1)
        error = self.fail(self.sdk.APIConnectionError('mock'))
        self.assertEqual(error.code, 'API_CONNECTION_ERROR')
        self.assertEqual(self.calls, 2)

    def test_certificate_cause_kept_without_raw_message_secrets(self):
        exc = self.sdk.APIConnectionError('key=sk-TEST-SECRET proxy=https://private-host/')
        exc.__cause__ = ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED sk-TEST-SECRET')
        error = self.fail(exc)
        self.assertTrue(error.diagnostics['certificate_error_hint'])
        self.assertEqual(error.diagnostics['exception_chain'], ['APIConnectionError', 'SSLCertVerificationError'])
        serialized = json.dumps(error.diagnostics) + str(error)
        self.assertNotIn('sk-TEST-SECRET', serialized)
        self.assertNotIn('private-host', serialized)

    def test_server_and_auth_status_not_merged(self):
        exc = self.sdk.APIStatusError('mock')
        exc.status_code, exc.request_id = 503, 'req_fixture'
        error = self.fail(exc)
        self.assertEqual(error.code, 'API_SERVER_ERROR')
        self.assertEqual(error.diagnostics['http_status'], 503)
        self.assertEqual(error.diagnostics['request_id'], 'req_fixture')
        exc = self.sdk.AuthenticationError('mock')
        exc.status_code = 401
        error = self.fail(exc)
        self.assertEqual(error.code, 'API_AUTH_ERROR')
        self.assertFalse(error.uncertain)

    def test_cyclic_causes_are_bounded(self):
        error = ValueError('fixture')
        error.__cause__ = error
        details = exception_details(error, 1, 15)
        self.assertEqual(details['exception_chain'], ['ValueError'])

    def test_probe_only_lists_models_and_disables_sdk_retries(self):
        settings, calls = {}, []
        class Client:
            def __init__(self, **kwargs):
                settings.update(kwargs)
                self.models = SimpleNamespace(list=lambda: calls.append('models.list'))
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch.dict('sys.modules', {'openai': self.sdk}):
            report = probe_models('fixture-key', Client)
        self.assertEqual(calls, ['models.list'])
        self.assertEqual(settings['max_retries'], 0)
        self.assertEqual(settings['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(report['code'], 'API_CONNECTION_OK')
        self.assertNotIn('fixture-key', json.dumps(report))

    def test_probe_timeout_is_failure_not_false_success(self):
        error = self.sdk.APITimeoutError('fixture')
        def fail(): raise error
        class Client:
            def __init__(self, **kwargs): self.models = SimpleNamespace(list=fail)
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch.dict('sys.modules', {'openai': self.sdk}):
            report = probe_models('fixture-key', Client)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual(report['code'], 'API_TIMEOUT')

    def test_engine_saves_diagnostics_in_summary_and_audit(self):
        with TemporaryDirectory() as name:
            root = Path(name)
            write_json(root / 'config_v6.json', {'model': 'fixture-model'})
            spec = make_spec(load_analysis_config(root))
            item, source = make_item(root)
            folder = root / 'out' / 'run'
            folder.mkdir(parents=True)
            detail = {'exception_chain': ['APITimeoutError', 'ReadTimeout'],
                      'elapsed_seconds': 120.1, 'timeout_seconds': 120}
            def fail(): raise APIError('API_TIMEOUT', 'fixture', True, detail)
            fake = FakeAnalyzer(response_for(candidate_for(source)), fail)
            report = {}
            with redirect_stdout(io.StringIO()), patch('v753.analysis_engine.load_api_key', return_value=''):
                analyze_articles([item], spec, folder, report,
                    lambda: write_json(folder / 'summary.json', report), analyzer_factory=lambda key, cfg: fake)
            self.assertEqual(report['analysis_results'][0]['diagnostics'], detail)
            audit = read_json(folder / 'analysis_audit.json')
            self.assertEqual(audit['items'][0]['error']['diagnostics'], detail)
            self.assertEqual(audit['items'][0]['request_execution'], 'unknown')


if __name__ == '__main__':
    unittest.main()
