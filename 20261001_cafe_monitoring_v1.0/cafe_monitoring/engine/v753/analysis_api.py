"""기존 V6와 같은 공식 Responses API. 자동 재시도는 하지 않습니다."""
import json
from .core import V7Error
from .api_diagnostics import APIError, run_sdk_call
from .api_transport import create_openai_client


class Analyzer:
    def __init__(self, key, spec, client=None):
        self.timeout_seconds = spec['timeout_seconds']
        if client is not None:
            self.client = client
        else:
            self.client = create_openai_client(key, spec['timeout_seconds'])

    def analyze(self, request):
        response = run_sdk_call(lambda: self.client.responses.create(**request), self.timeout_seconds)
        return response.model_dump(mode='json')

    def close(self):
        self.client.close()


def decode_response(response):
    if response.get('status') != 'completed':
        code = 'OUTPUT_LIMIT' if (response.get('incomplete_details') or {}).get('reason') == 'max_output_tokens' else 'RESPONSE_INCOMPLETE'
        raise V7Error(code, 'AI 응답이 완성되지 않았습니다. 응답·사용량을 저장하고 해당 글은 제외합니다.')
    texts = []
    for item in response.get('output') or []:
        if item.get('type') != 'message':
            continue
        for part in item.get('content') or []:
            if part.get('type') == 'refusal':
                raise V7Error('MODEL_REFUSAL', 'AI가 분석을 거절했습니다. 자동 재호출하지 않습니다.')
            if part.get('type') == 'output_text':
                texts.append(part.get('text', ''))
    try:
        data = json.loads(''.join(texts))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise V7Error('INVALID_RESPONSE_JSON', '응답을 분석 JSON 객체로 읽지 못했습니다.') from None


def response_usage(response):
    usage = response.get('usage') or {}
    return {'reported': bool(usage), **{k: usage.get(k) if type(usage.get(k)) is int else None
                                     for k in ('input_tokens', 'output_tokens', 'total_tokens')}}
