"""재시도 정책: 지수 백오프 + 최대 시도 횟수."""
from __future__ import annotations

from ..common import iso_after
from ..config import RetrySettings


def retry_delay_seconds(attempts: int, policy: RetrySettings) -> float:
    """attempts번째 실패 후 다음 시도까지 대기 시간(초)."""
    exponent = max(0, attempts - 1)
    minutes = min(policy.base_delay_minutes * (2 ** exponent), policy.max_delay_minutes)
    return minutes * 60.0


def schedule_retry(attempts: int, policy: RetrySettings) -> tuple[str, str | None]:
    """실패 처리 결과 상태와 다음 재시도 시각을 돌려줍니다.

    반환: ("failed", next_retry_at) 또는 ("error", None)  (최대 횟수 초과)
    """
    if attempts >= policy.max_attempts:
        return "error", None
    return "failed", iso_after(retry_delay_seconds(attempts, policy))
