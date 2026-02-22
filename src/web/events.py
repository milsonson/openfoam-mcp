"""In-memory event pub/sub for job progress streaming."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator

from .artifacts import ensure_job_id


_subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)


def publish_job_event(job_id: str, event_json_line: str) -> None:
    """Publish a serialized JSON event line to current subscribers."""
    safe_job_id = ensure_job_id(job_id)
    queues = list(_subscribers.get(safe_job_id, set()))
    for queue in queues:
        try:
            queue.put_nowait(event_json_line)
        except asyncio.QueueFull:
            continue


@asynccontextmanager
async def subscribe_job_events(job_id: str) -> AsyncIterator[asyncio.Queue[str]]:
    """Subscribe to in-memory live events for a job."""
    safe_job_id = ensure_job_id(job_id)
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=512)
    _subscribers[safe_job_id].add(queue)
    try:
        yield queue
    finally:
        _subscribers[safe_job_id].discard(queue)
        if not _subscribers[safe_job_id]:
            _subscribers.pop(safe_job_id, None)

