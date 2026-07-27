"""Event Bus port and delivery acknowledgements."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from low_altitude_ai.domain.envelope import Envelope

EventHandler = Callable[[Envelope], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PublishAck:
    accepted: bool
    message_id: str


class Subscription(Protocol):
    async def close(self) -> None: ...


class EventBusPort(Protocol):
    async def publish(self, topic: str, message: Envelope) -> PublishAck: ...

    def subscribe(self, topic: str, handler: EventHandler, *, group: str) -> Subscription: ...

    async def request(self, topic: str, command: Envelope, timeout_s: float) -> Envelope: ...
