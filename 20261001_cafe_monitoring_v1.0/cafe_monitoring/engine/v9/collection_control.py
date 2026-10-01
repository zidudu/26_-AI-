"""Task-local log labels and a shared navigation gate; no browser data is logged."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import time

from v8.configuration import V8Error

_cafe = ContextVar('v92_cafe', default='')
_keyword = ContextVar('v92_keyword', default='')
_gate = ContextVar('v92_gate', default=None)
ACCESS_CODES = {'REQUEST_BLOCKED', 'LOGIN_REQUIRED', 'AUTH_REQUIRED'}


def log(*values, **kwargs):
    prefix = ''.join('[' + value + ']' for value in (_cafe.get(), _keyword.get()) if value)
    print(*(([prefix] if prefix else []) + list(values)), **kwargs)


def keyword(value):
    _keyword.set(value)


@contextmanager
def task_scope(cafe, gate):
    tokens = (_cafe.set(cafe), _keyword.set(''), _gate.set(gate))
    try:
        yield
    finally:
        for var, token in zip((_cafe, _keyword, _gate), tokens):
            var.reset(token)


def check_stop():
    gate = _gate.get()
    if gate is not None:
        gate.check()


def report_access_error(exc):
    gate = _gate.get()
    if gate is not None and getattr(exc, 'code', '') in ACCESS_CODES:
        gate.stop(exc.code)


async def pace():
    gate = _gate.get()
    if gate is None:
        raise RuntimeError('The asynchronous collector requires a shared request gate.')
    await gate.wait()


class RequestGate:
    def __init__(self, interval):
        self.interval = max(0.0, float(interval))
        self.lock = asyncio.Lock()
        self.last = None
        self.reason = None
        self.grants = 0

    def stop(self, code):
        if self.reason is None or code == 'USER_CANCELLED':
            self.reason = code

    def check(self):
        if self.reason:
            code = 'USER_CANCELLED' if self.reason == 'USER_CANCELLED' else 'DEFERRED_' + self.reason
            raise V8Error(code, '전체 수집의 새 요청을 중단했습니다: ' + self.reason)

    async def wait(self):
        async with self.lock:
            self.check()
            remaining = 0 if self.last is None else self.interval - (time.monotonic() - self.last)
            while remaining > 0:
                await asyncio.sleep(min(remaining, 0.25))
                self.check()
                remaining = self.interval - (time.monotonic() - self.last)
            self.check()
            self.last = time.monotonic()
            self.grants += 1
