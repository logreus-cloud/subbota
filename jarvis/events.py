from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from jarvis.db import Database


class EventBus:
    def __init__(self, db: Database, loop: asyncio.AbstractEventLoop) -> None:
        self.db = db
        self.loop = loop
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, type: str, **payload: Any) -> dict[str, Any]:
        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        id = 0 if type in {"status", "assistant_delta"} else self.db.add_event(ts, type, payload)
        event = {"id": id, "ts": ts, "type": type, **payload}
        try:
            in_loop = asyncio.get_running_loop() is self.loop
        except RuntimeError:
            in_loop = False
        if in_loop:
            self._deliver(event)
        else:
            self.loop.call_soon_threadsafe(self._deliver, event)
        return event

    def _deliver(self, event: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)
