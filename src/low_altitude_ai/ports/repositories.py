"""Persistence ports; implementations may use SQLite, PostgreSQL or event storage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from low_altitude_ai.domain.envelope import Envelope


@dataclass(frozen=True, slots=True)
class EventQuery:
    run_id: UUID
    from_time: datetime
    to_time: datetime
    topics: tuple[str, ...] = ()
    vehicle_id: str | None = None


class EventRepository(Protocol):
    async def append(self, topic: str, event: Envelope) -> bool: ...

    def read(self, query: EventQuery) -> AsyncIterator[Envelope]: ...
