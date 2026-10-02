"""Bounded latest-state SSE hub. Worker-manager thread publishes; browsers do not poll logs.

A slow browser receives only the latest snapshot, never an unbounded event backlog.
The initial snapshot always reconciles state after reconnect/server restart.
"""
from __future__ import annotations
import asyncio
import threading


class JobEvents:
    def __init__(self):
        self._lock = threading.RLock()
        self._listeners = {}
        self._snapshot = {'revision': 0, 'library_revision': 0, 'jobs': []}
        self._closed = False

    @property
    def subscriber_count(self):
        with self._lock:
            return len(self._listeners)

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    @staticmethod
    def _offer(queue, value):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(value)

    def subscribe(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        with self._lock:
            if len(self._listeners) >= 8:
                raise ValueError('실시간 연결은 최대 8개입니다.')
            self._listeners[queue] = loop
            self._offer(queue, None if self._closed else self.snapshot())
        return queue

    def unsubscribe(self, queue):
        with self._lock:
            self._listeners.pop(queue, None)

    def publish(self, jobs, library_changed=False):
        with self._lock:
            if self._closed:
                return
            if not library_changed and jobs == self._snapshot['jobs']:
                return
            self._snapshot = {
                'revision': self._snapshot['revision'] + 1,
                'library_revision': self._snapshot['library_revision'] + int(library_changed),
                'jobs': jobs,
            }
            packet = self.snapshot()
            for queue, loop in list(self._listeners.items()):
                try:
                    loop.call_soon_threadsafe(self._offer, queue, packet)
                except RuntimeError:
                    self._listeners.pop(queue, None)

    def close(self):
        with self._lock:
            self._closed = True
            for queue, loop in list(self._listeners.items()):
                try:
                    loop.call_soon_threadsafe(self._offer, queue, None)
                except RuntimeError:
                    pass
