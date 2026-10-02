"""요청 간 최소 간격을 보장하는 단순 속도 제한기. 중지 이벤트가 오면 대기를 즉시 끊습니다."""
from __future__ import annotations

import random
import threading
import time


class RateLimiter:
    def __init__(self, min_interval: float, jitter: float = 0.0, sleep=time.sleep, clock=time.monotonic,
                 interrupt: threading.Event | None = None):
        self.min_interval = max(0.0, min_interval)
        self.jitter = max(0.0, jitter)
        self._sleep = sleep
        self._clock = clock
        self._interrupt = interrupt
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> float:
        """필요한 만큼 대기하고 실제 대기 시간을 반환합니다. 중지 이벤트가 설정되면 바로 돌아옵니다."""
        with self._lock:
            now = self._clock()
            target = self.min_interval + (random.uniform(0, self.jitter) if self.jitter else 0.0)
            waited = 0.0
            if self._last is not None:
                remaining = self._last + target - now
                if remaining > 0:
                    if self._interrupt is not None:
                        started = self._clock()
                        self._interrupt.wait(remaining)
                        waited = self._clock() - started
                    else:
                        self._sleep(remaining)
                        waited = remaining
            self._last = self._clock()
            return waited
