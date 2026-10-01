"""공식 SDK 오류의 종류와 시간만 기록합니다. 키·헤더·환경변수 값은 기록하지 않습니다."""
import re
import socket
import time
from .core import V7Error

BUILD = '7.5.4_api_diagnostics_20260917'


class APIError(V7Error):
    def __init__(self, code, message, uncertain=False, diagnostics=None):
        super().__init__(code, message)
        self.uncertain = uncertain
        self.stop = True
        self.diagnostics = diagnostics or {}


def exception_details(exc, elapsed, timeout_seconds):
    chain, seen, current = [], set(), exc
    tls_hint = dns_hint = proxy_hint = False
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(type(current).__name__)
        # 원문 예외 메시지는 주소·키를 포함할 수 있으므로 저장하지 않습니다.
        message = str(current).lower()
        tls_hint |= any(word in message for word in ('certificate verify failed', 'certificate_verify_failed', 'self signed certificate', 'unable to get local issuer'))
        dns_hint |= isinstance(current, socket.gaierror) or any(word in message for word in ('getaddrinfo failed', 'name resolution'))
        proxy_hint |= 'proxy' in type(current).__name__.lower()
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    details = {'exception_chain': chain, 'elapsed_seconds': round(max(0, elapsed), 3),
               'timeout_seconds': timeout_seconds, 'certificate_error_hint': tls_hint,
               'dns_error_hint': dns_hint, 'proxy_error_hint': proxy_hint}
    status = getattr(exc, 'status_code', None)
    if type(status) is int:
        details['http_status'] = status
    request_id = getattr(exc, 'request_id', None)
    if isinstance(request_id, str) and re.fullmatch(r'req_[A-Za-z0-9_-]{1,100}', request_id):
        details['request_id'] = request_id
    return details


def run_sdk_call(operation, timeout_seconds):
    """자체 재시도 없음. 호출자는 SDK에도 max_retries=0을 지정해야 합니다."""
    import openai
    started = time.monotonic()
    try:
        return operation()
    except openai.OpenAIError as exc:
        details = exception_details(exc, time.monotonic() - started, timeout_seconds)
        uncertain = False
        if isinstance(exc, openai.APITimeoutError):
            code, message, uncertain = 'API_TIMEOUT', 'API 요청의 대기 시간이 초과됐습니다. 서버 처리 완료 여부는 미확정입니다.', True
        elif isinstance(exc, openai.APIConnectionError):
            code, message, uncertain = 'API_CONNECTION_ERROR', 'API 연결 중 오류가 발생했습니다. 하위 예외와 인증서·DNS 단서를 확인하세요.', True
        elif isinstance(exc, openai.AuthenticationError):
            code, message = 'API_AUTH_ERROR', '기존 02_setup_key_v6.bat에서 API 키를 확인하세요.'
        elif isinstance(exc, openai.PermissionDeniedError):
            code, message = 'API_PERMISSION_ERROR', 'API 프로젝트의 사용 권한을 확인하세요.'
        elif isinstance(exc, openai.NotFoundError):
            code, message = 'MODEL_NOT_AVAILABLE', '모델명 또는 API 경로를 확인하세요.'
        elif isinstance(exc, openai.RateLimitError):
            code, message = 'API_RATE_LIMIT', 'API 잔액·한도 또는 호출 속도 제한을 확인하세요.'
        elif isinstance(exc, openai.APIStatusError):
            if exc.status_code >= 500:
                code, message, uncertain = 'API_SERVER_ERROR', 'API 서버 오류 응답을 받았습니다. 자동 재호출하지 않습니다.', True
            else:
                code, message = 'API_REQUEST_ERROR', 'API 요청 설정을 확인하세요.'
        else:
            code, message = 'API_CLIENT_ERROR', '공식 SDK 호출에 실패했습니다.'
        raise APIError(code, message, uncertain, details) from None
