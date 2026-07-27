"""Event-driven service that projects accepted inputs into Twin snapshots."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from low_altitude_ai.digital_twin.store import TwinStateStore
from low_altitude_ai.domain import Envelope, Source
from low_altitude_ai.domain.identifiers import uuid7
from low_altitude_ai.ports.clock import ClockPort
from low_altitude_ai.ports.event_bus import EventBusPort, Subscription

EventIdFactory = Callable[[datetime], UUID]


def _default_event_id(at: datetime) -> UUID:
    return uuid7(unix_ms=int(at.timestamp() * 1_000))


class TwinIngestService:
    """Consume validated events and publish one immutable snapshot per revision."""

    def __init__(
        self,
        *,
        store: TwinStateStore,
        event_bus: EventBusPort,
        clock: ClockPort,
        event_id_factory: EventIdFactory = _default_event_id,
        topics: tuple[str, ...] = ("sensor.normalized.*",),
    ) -> None:
        if not topics:
            raise ValueError("at least one Twin input topic is required")
        self._store = store
        self._event_bus = event_bus
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._topics = topics
        self._subscriptions: list[Subscription] = []

    async def start(self) -> None:
        if self._subscriptions:
            return
        for topic in self._topics:
            self._subscriptions.append(
                self._event_bus.subscribe(
                    topic,
                    self._handle,
                    group=f"twin-ingest:{topic}",
                )
            )

    async def stop(self) -> None:
        subscriptions = tuple(self._subscriptions)
        self._subscriptions.clear()
        for subscription in subscriptions:
            await subscription.close()

    async def _handle(self, event: Envelope) -> None:
        outcome = self._store.apply(event)
        if not outcome.applied:
            return
        snapshot = outcome.snapshot
        now = self._clock.now()
        event_id = self._event_id_factory(now)
        snapshot_event = Envelope(
            schema="twin.snapshot/1.0",
            event_id=event_id,
            trace_id=event.trace_id,
            causation_id=event.event_id,
            source=Source(service="twin-service", instance_id=snapshot.twin_id),
            observed_at=snapshot.watermark,
            received_at=now,
            monotonic_ns=self._clock.monotonic_ns(),
            run_id=event.run_id,
            mode=event.mode,
            vehicle_id=event.vehicle_id,
            sequence=snapshot.revision,
            quality=snapshot.quality,
            payload=snapshot.to_payload(),
        )
        await self._event_bus.publish("twin.snapshot", snapshot_event)
