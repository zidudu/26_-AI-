"""Bounded, process-local activity stream for the visible log panel."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Lock


_events: deque[dict] = deque(maxlen=500)
_lock = Lock()
_next_id = 0


def emit(kind: str, message: str, *, level: str = "info", operation: str | None = None) -> dict:
    """Record stages only. Callers must not put prompt or document contents here."""
    global _next_id
    with _lock:
        _next_id += 1
        item = {
            "id": _next_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "level": level,
            "operation": operation,
            "message": message,
        }
        _events.append(item)
        return item


def recent(since: int = 0, limit: int = 150) -> dict:
    with _lock:
        visible = [item for item in _events if item["id"] > since]
        if since == 0:
            visible = visible[-limit:]
        else:
            visible = visible[:limit]
        return {"events": visible, "last_id": _next_id}
