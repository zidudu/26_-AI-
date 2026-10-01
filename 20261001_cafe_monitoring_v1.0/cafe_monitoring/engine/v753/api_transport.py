"""V7.5.3 API 클라이언트에만 OS 인증서 저장소를 연결합니다."""
import os
import ssl
import sys
from .core import V7Error

TLS_BUILD = '7.5.3_windows_truststore_20260917'


def certificate_policy(platform_name=None, environment=None):
    platform_name = sys.platform if platform_name is None else platform_name
    environment = os.environ if environment is None else environment
    # 사용자가 명시한 CA 환경 설정은 SDK가 그대로 처리하도록 둡니다.
    if environment.get('SSL_CERT_FILE') or environment.get('SSL_CERT_DIR'):
        return 'explicit_ca_environment'
    return 'windows_system_truststore' if platform_name == 'win32' else 'sdk_default'


def windows_ssl_context():
    try:
        import truststore
    except ImportError:
        raise V7Error('TRUSTSTORE_MISSING', '08_install_windows_tls_v753.bat를 실행해 인증서 연동 패키지를 설치하세요.') from None
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise V7Error('TLS_VERIFICATION_REQUIRED', '인증서 및 호스트 이름 검증이 활성화된 컨텍스트가 필요합니다.')
    return context


def create_openai_client(key, timeout_seconds):
    try:
        from openai import OpenAI
    except ImportError:
        raise V7Error('OPENAI_MISSING', '01_install_v753.bat를 실행해 API 패키지를 설치하세요.') from None
    options = {'api_key': key, 'base_url': 'https://api.openai.com/v1',
               'timeout': timeout_seconds, 'max_retries': 0}
    if certificate_policy() != 'windows_system_truststore':
        return OpenAI(**options)
    import httpx
    client = httpx.Client(verify=windows_ssl_context(), timeout=timeout_seconds,
                          follow_redirects=True, trust_env=True)
    try:
        return OpenAI(**options, http_client=client)
    except BaseException:
        client.close()
        raise
