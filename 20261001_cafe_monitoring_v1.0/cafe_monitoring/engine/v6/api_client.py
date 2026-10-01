"""OpenAI 공식 SDK + Responses API. 응답을 먼저 보관해 검증 실패의 사용량도 남깁니다."""
import json
import openai
import ssl
import httpx
import truststore
# Windows certificate validation
from .common import V6Error


def build_request(article, spec):
    payload = {"focus_topics": spec["focus_topics"], "article": {
        "title": article["title"], "body": article["body"], "written_at": article["written_at"]}}
    if spec["schema_version"] in ("6.2", "6.3"):
        from .evidence import EVIDENCE_VERSION, source_units
        if spec.get("evidence_version") != EVIDENCE_VERSION:
            raise V6Error("PLAN_CHANGED", "지원하지 않는 원문 번호 기준입니다.")
        payload["evidence_units"] = source_units(article)
    request = {"model": spec["model"], "instructions": spec["instructions"],
               "input": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
               "text": {"format": {"type": "json_schema", "name": "cafe_quality_analysis",
                                    "strict": True, "schema": spec["schema"]}},
               "max_output_tokens": spec["max_output_tokens"], "store": False}
    if spec["reasoning_effort"] is not None:
        request["reasoning"] = {"effort": spec["reasoning_effort"]}
    return request


class OpenAIAnalyzer:
    def __init__(self, key, spec, client=None):
        self.spec = spec
        # SDK 자동 재시도를 끄고, 확인되지 않은 응답의 중복 청구 가능성을 드러냅니다.
        self.client = client or openai.OpenAI(api_key=key, base_url="https://api.openai.com/v1",
                                             timeout=spec["timeout_seconds"], max_retries=0,
                                             http_client=httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))

    def close(self):
        self.client.close()

    def analyze(self, article):
        request = build_request(article, self.spec)
        try:
            response = self.client.responses.create(**request)
            return response.model_dump(mode="json")
        except openai.AuthenticationError:
            raise V6Error("API_AUTH_ERROR", "API 키를 확인하세요. 02_setup_key_v6.bat에서 교체할 수 있습니다.", stop=True) from None
        except openai.PermissionDeniedError:
            raise V6Error("API_PERMISSION_ERROR", "API 프로젝트의 모델 사용 권한을 확인하세요.", stop=True) from None
        except openai.NotFoundError:
            raise V6Error("MODEL_NOT_AVAILABLE", "config_v6.json의 모델명과 해당 프로젝트의 모델 접근 권한을 확인하세요.", stop=True) from None
        except openai.RateLimitError as exc:
            quota = getattr(exc, "code", None) == "insufficient_quota"
            raise V6Error("API_QUOTA" if quota else "API_RATE_LIMIT",
                          "API 잔액·한도 또는 호출 속도 제한을 확인한 뒤 07_retry_v6.bat로 재시도하세요.", stop=True) from None
        except (openai.APITimeoutError, openai.APIConnectionError):
            raise V6Error("API_RESULT_UNKNOWN", "응답 수신 여부를 확인하지 못했습니다. 재시도하면 추가 사용량이 발생할 수 있습니다.",
                          stop=True, uncertain=True) from None
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise V6Error("API_RESULT_UNKNOWN", "서버 오류로 응답이 확인되지 않았습니다. 07_retry_v6.bat에서 재시도할 수 있습니다.",
                              stop=True, uncertain=True) from None
            raise V6Error("API_REQUEST_ERROR", "모델·추론 강도·응답 형식 설정이 API와 호환되는지 확인하세요.", stop=True) from None
        except openai.OpenAIError:
            raise V6Error("API_CLIENT_ERROR", "OpenAI SDK 호출에 실패했습니다. 설치 상태를 확인하세요.", stop=True) from None


def usage_from_response(response):
    usage = response.get("usage") or {}
    def count(value):
        return value if type(value) is int and value >= 0 else 0
    return {"input_tokens": count(usage.get("input_tokens")),
            "cached_input_tokens": count((usage.get("input_tokens_details") or {}).get("cached_tokens")),
            "output_tokens": count(usage.get("output_tokens")),
            "reasoning_tokens": count((usage.get("output_tokens_details") or {}).get("reasoning_tokens")),
            "total_tokens": count(usage.get("total_tokens")),
            "reported": bool(response.get("usage"))}


def decode_response(response):
    if response.get("status") != "completed":
        reason = (response.get("incomplete_details") or {}).get("reason")
        code = "OUTPUT_LIMIT" if reason == "max_output_tokens" else "RESPONSE_INCOMPLETE"
        raise V6Error(code, "AI 응답이 끝나지 않았습니다. 사용량은 기록하고 성공으로 저장하지 않습니다.")
    texts = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise V6Error("MODEL_REFUSAL", "AI가 이 게시글의 분석을 거절했습니다. 자동으로 재호출하지 않습니다.")
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    try:
        return json.loads("".join(texts))
    except (ValueError, TypeError):
        raise V6Error("INVALID_RESPONSE_JSON", "AI 응답을 JSON으로 읽지 못했습니다.") from None
