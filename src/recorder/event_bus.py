from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Event:
    topic: str
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class EventBus:
    _instance: EventBus | None = None

    @classmethod
    def get_instance(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._sync_subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=200)
        self._subscribers.setdefault(topic, []).append(queue)
        return queue

    def subscribe_sync(self, topic: str, callback: Callable) -> None:
        self._sync_subscribers.setdefault(topic, []).append(callback)

    def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(topic, [])
        if queue in queues:
            queues.remove(queue)

    def unsubscribe_sync(self, topic: str, callback: Callable) -> None:
        callbacks = self._sync_subscribers.get(topic, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        event = Event(topic=topic, payload=payload)

        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

        for callback in self._sync_subscribers.get(topic, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.debug(f"Event callback error for topic '{topic}': {e}")

    def publish_sync(self, topic: str, payload: dict[str, Any]) -> None:
        event = Event(topic=topic, payload=payload)

        for queue in self._subscribers.get(topic, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)

        for callback in self._sync_subscribers.get(topic, []):
            try:
                callback(event)
            except Exception as e:
                logger.debug(f"Sync event callback error for topic '{topic}': {e}")

    def clear(self) -> None:
        self._subscribers.clear()
        self._sync_subscribers.clear()
