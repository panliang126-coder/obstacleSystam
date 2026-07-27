"""Bounded in-process event bus for development, simulation and tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import suppress

from low_altitude_ai.domain.envelope import Envelope
from low_altitude_ai.ports.event_bus import EventHandler, PublishAck


class EventBusClosedError(RuntimeError):
    """An operation was attempted after the bus or subscription was closed."""


class EventBusFullError(RuntimeError):
    """A subscriber did not accept a message within the configured deadline."""


class HandlerFailedError(RuntimeError):
    """A subscription handler failed while processing an accepted message."""


def _topic_matches(pattern: str, topic: str) -> bool:
    if pattern.endswith(".*"):
        prefix = pattern[:-1]
        return topic.startswith(prefix) and len(topic) > len(prefix)
    return pattern == topic


class InMemorySubscription:
    """One bounded consumer with explicit drain and shutdown behavior."""

    def __init__(
        self,
        *,
        bus: InMemoryEventBus,
        topic: str,
        group: str,
        handler: EventHandler,
        queue_size: int,
    ) -> None:
        self.topic = topic
        self.group = group
        self._bus = bus
        self._handler = handler
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=queue_size)
        self._failure: BaseException | None = None
        self._closed = False
        self._worker = asyncio.create_task(
            self._run(),
            name=f"in-memory-bus:{group}:{topic}",
        )

    @property
    def closed(self) -> bool:
        return self._closed

    async def enqueue(self, message: Envelope, timeout_s: float) -> None:
        if self._closed:
            raise EventBusClosedError(f"subscription {self.group!r} is closed")
        self._raise_if_failed()
        try:
            await asyncio.wait_for(self._queue.put(message), timeout=timeout_s)
        except asyncio.TimeoutError as error:  # noqa: UP041 - host Python 3.10 compatibility
            raise EventBusFullError(
                f"subscription {self.group!r} queue is full for topic {self.topic!r}"
            ) from error

    async def join(self) -> None:
        """Wait until every accepted message has completed handler processing."""

        await self._queue.join()
        self._raise_if_failed()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._remove(self)
        await self._queue.join()
        self._worker.cancel()
        with suppress(asyncio.CancelledError):
            await self._worker
        self._raise_if_failed()

    async def _run(self) -> None:
        while True:
            message = await self._queue.get()
            try:
                await self._handler(message)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self._failure is None:
                    self._failure = error
            finally:
                self._queue.task_done()

    def _raise_if_failed(self) -> None:
        if self._failure is not None:
            raise HandlerFailedError(
                f"handler for group {self.group!r} and topic {self.topic!r} failed"
            ) from self._failure


class InMemoryEventBus:
    """A deterministic, bounded EventBus adapter.

    Delivery is at-most-once inside a process. A publish acknowledgement means that every
    matching subscriber accepted the event into its bounded queue, not that handlers have
    completed. Call :meth:`drain` when a simulation tick requires a processing barrier.
    """

    def __init__(
        self,
        *,
        queue_size: int = 128,
        publish_timeout_s: float = 0.1,
        truth_access_groups: Iterable[str] = (),
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        if publish_timeout_s <= 0:
            raise ValueError("publish_timeout_s must be positive")
        self._queue_size = queue_size
        self._publish_timeout_s = publish_timeout_s
        self._truth_access_groups = frozenset(truth_access_groups)
        self._subscriptions: list[InMemorySubscription] = []
        self._closed = False

    async def publish(self, topic: str, message: Envelope) -> PublishAck:
        self._ensure_open()
        if not topic.strip():
            raise ValueError("topic must be non-empty")
        targets = tuple(
            subscription
            for subscription in self._subscriptions
            if not subscription.closed and _topic_matches(subscription.topic, topic)
        )
        for subscription in targets:
            await subscription.enqueue(message, self._publish_timeout_s)
        return PublishAck(accepted=True, message_id=str(message.event_id))

    def subscribe(
        self,
        topic: str,
        handler: EventHandler,
        *,
        group: str,
    ) -> InMemorySubscription:
        self._ensure_open()
        if not topic.strip() or not group.strip():
            raise ValueError("topic and group must be non-empty")
        if topic.startswith("simulation.truth.") and group not in self._truth_access_groups:
            raise PermissionError(f"group {group!r} cannot subscribe to simulation truth")
        if any(
            item.topic == topic and item.group == group and not item.closed
            for item in self._subscriptions
        ):
            raise ValueError(f"group {group!r} already subscribes to {topic!r}")
        subscription = InMemorySubscription(
            bus=self,
            topic=topic,
            group=group,
            handler=handler,
            queue_size=self._queue_size,
        )
        self._subscriptions.append(subscription)
        return subscription

    async def request(self, topic: str, command: Envelope, timeout_s: float) -> Envelope:
        del topic, command, timeout_s
        raise NotImplementedError("request/reply is not part of the Phase 2 in-process adapter")

    async def drain(self) -> None:
        """Wait for all accepted work, including work published by handlers."""

        while True:
            subscriptions = tuple(self._subscriptions)
            for subscription in subscriptions:
                await subscription.join()
            if all(subscription._queue.empty() for subscription in subscriptions):
                return

    async def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        for subscription in tuple(self._subscriptions):
            try:
                await subscription.close()
            except HandlerFailedError as error:
                errors.append(error)
        self._closed = True
        if errors:
            raise HandlerFailedError(f"{len(errors)} subscription handler(s) failed") from errors[0]

    def _remove(self, subscription: InMemorySubscription) -> None:
        with suppress(ValueError):
            self._subscriptions.remove(subscription)

    def _ensure_open(self) -> None:
        if self._closed:
            raise EventBusClosedError("event bus is closed")
