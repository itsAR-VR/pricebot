import asyncio
from collections import defaultdict
from typing import Any, DefaultDict, Optional, Set


class JobEventBroker:
    """Simple in-memory pub/sub for job status events."""

    def __init__(self) -> None:
        self._queues: DefaultDict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def subscribe(self, conversation_id: str) -> asyncio.Queue:
        loop = asyncio.get_running_loop()
        self._loop = loop
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[conversation_id].add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue) -> None:
        listeners = self._queues.get(conversation_id)
        if not listeners:
            return
        listeners.discard(queue)
        if not listeners:
            self._queues.pop(conversation_id, None)

    def publish(self, conversation_id: Optional[str], payload: dict[str, Any]) -> None:
        if not conversation_id:
            return
        loop = self._loop
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self._dispatch, conversation_id, payload)

    def _dispatch(self, conversation_id: str, payload: dict[str, Any]) -> None:
        queues = self._queues.get(conversation_id)
        if not queues:
            return
        for queue in list(queues):
            queue.put_nowait(payload)


job_event_broker = JobEventBroker()

__all__ = ["job_event_broker", "JobEventBroker"]
