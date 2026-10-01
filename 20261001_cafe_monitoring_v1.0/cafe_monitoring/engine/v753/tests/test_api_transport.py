"""클라이언트 연결·검증 설정 검사. 회사 Windows 인증서 체인을 재현하지 않습니다."""
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from v753.api_transport import certificate_policy, windows_ssl_context, create_openai_client
from v753.analysis_api import Analyzer
from v753.core import V7Error
from v753.diagnose_api import probe_models
from v753.tests.test_api_diagnostics import sdk_fixture


class TransportTests(unittest.TestCase):
    def test_windows_uses_system_store_but_explicit_ca_is_preserved(self):
        self.assertEqual(certificate_policy('win32', {}), 'windows_system_truststore')
        self.assertEqual(certificate_policy('linux', {}), 'sdk_default')
        for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR'):
            self.assertEqual(certificate_policy('win32', {key: 'fixture-path'}), 'explicit_ca_environment')

    def test_unverified_context_is_rejected(self):
        for hostname, mode in ((False, ssl.CERT_REQUIRED), (True, ssl.CERT_NONE)):
            context = SimpleNamespace(check_hostname=hostname, verify_mode=mode)
            with patch.dict('sys.modules', {'truststore': SimpleNamespace(SSLContext=lambda _: context)}):
                with self.assertRaises(V7Error) as error:
                    windows_ssl_context()
                self.assertEqual(error.exception.code, 'TLS_VERIFICATION_REQUIRED')

    def test_truststore_context_keeps_host_and_certificate_checks(self):
        context = SimpleNamespace(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
        factory = Mock(return_value=context)
        with patch.dict('sys.modules', {'truststore': SimpleNamespace(SSLContext=factory)}):
            self.assertIs(windows_ssl_context(), context)
        factory.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)

    def test_windows_client_passes_verified_context_and_retains_proxy_settings(self):
        context, client, sdk_client = object(), Mock(), Mock()
        http_factory, sdk_factory = Mock(return_value=client), Mock(return_value=sdk_client)
        with patch('v753.api_transport.certificate_policy', return_value='windows_system_truststore'), \
             patch('v753.api_transport.windows_ssl_context', return_value=context), \
             patch.dict('sys.modules', {'openai': SimpleNamespace(OpenAI=sdk_factory), 'httpx': SimpleNamespace(Client=http_factory)}):
            self.assertIs(create_openai_client('fixture-key', 120), sdk_client)
        self.assertIs(http_factory.call_args.kwargs['verify'], context)
        self.assertTrue(http_factory.call_args.kwargs['trust_env'])
        self.assertEqual(sdk_factory.call_args.kwargs['max_retries'], 0)
        self.assertIs(sdk_factory.call_args.kwargs['http_client'], client)

    def test_explicit_ca_uses_unchanged_sdk_path(self):
        factory = Mock()
        with patch('v753.api_transport.certificate_policy', return_value='explicit_ca_environment'), \
             patch('v753.api_transport.windows_ssl_context', side_effect=AssertionError('must preserve explicit CA')), \
             patch.dict('sys.modules', {'openai': SimpleNamespace(OpenAI=factory)}):
            create_openai_client('fixture-key', 15)
        self.assertNotIn('http_client', factory.call_args.kwargs)

    def test_sdk_constructor_failure_closes_http_client(self):
        client = Mock()
        with patch('v753.api_transport.certificate_policy', return_value='windows_system_truststore'), \
             patch('v753.api_transport.windows_ssl_context', return_value=object()), \
             patch.dict('sys.modules', {'openai': SimpleNamespace(OpenAI=Mock(side_effect=ValueError('fixture'))),
                                        'httpx': SimpleNamespace(Client=Mock(return_value=client))}):
            with self.assertRaises(ValueError):
                create_openai_client('fixture-key', 15)
        client.close.assert_called_once()

    def test_analysis_and_probe_both_use_shared_client_factory(self):
        client = Mock()
        with patch('v753.analysis_api.create_openai_client', return_value=client) as factory:
            self.assertIs(Analyzer('fixture-key', {'timeout_seconds': 120}).client, client)
        factory.assert_called_once_with('fixture-key', 120)
        probe_client = Mock()
        probe_client.__enter__ = Mock(return_value=probe_client)
        probe_client.__exit__ = Mock(return_value=False)
        with patch('v753.diagnose_api.create_openai_client', return_value=probe_client) as factory, \
             patch.dict('sys.modules', {'openai': sdk_fixture()}):
            self.assertEqual(probe_models('fixture-key')['code'], 'API_CONNECTION_OK')
        factory.assert_called_once_with('fixture-key', 15)
        probe_client.models.list.assert_called_once_with()
        probe_client.responses.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
