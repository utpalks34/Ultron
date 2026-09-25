"""In-process event bus.

This is the one input to the WebSocket layer: sensors, the VRAM poller and the
graph all publish here, and the WebSocket layer only ever reads from here. The
graph never knows a UI exists.
"""

import asyncio
import logging
import time
import uuid

log = logging.getLogger("ultron.bus")

LOSSY = {"audio_level", "vram", "token"}
CRITICAL = {"state", "error", "graph_node", "confirm_request", "transcript"}

QUEUE_MAX = 256


class EventBus:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def publish(self, evt: dict) -> None:
        evt.setdefault("v", 1)
        evt.setdefault("id", str(uuid.uuid4()))
        evt.setdefault("ts", int(time.time() * 1000))
        critical = evt.get("type") in CRITICAL
        for q in list(self._subs):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                if not critical:
                    continue  # lossy or unclassified: drop silently
                if self._drop_one_lossy(q):
                    try:
                        q.put_nowait(evt)
                    except asyncio.QueueFull:
                        log.warning("dropped critical %s: queue full", evt.get("type"))
                else:
                    log.warning("dropped critical %s: queue full, no lossy event to evict", evt.get("type"))

    @staticmethod
    def _drop_one_lossy(q: asyncio.Queue) -> bool:
        # asyncio.Queue has no public removal API; the backing deque is the only way to evict mid-queue.
        items = q._queue  # type: ignore[attr-defined]
        for i, queued in enumerate(items):
            if queued.get("type") in LOSSY:
                del items[i]
                return True
        return False
